from __future__ import annotations

import os
import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec
from echograph.ui import actions
from echograph.services.profiler import profiled
from echograph.material_debug import material_debug_log as _material_debug_log
import traceback

SUPPORTED_EXTS = {".fbx", ".bvh", ".obj", ".gltf", ".glb", ".ply", ".stl", ".off", ".om"}
_MATERIAL_KINDS = {"mnaterial", "material"}
_TEXTURE_KINDS = {"texture", "texture_pro", "texture_layer"}
_NORMALS_PROCESS_KINDS = {"normals", "normal", "smooth_normals", "smooth normals"}
_GEOMETRY_PROCESS_KINDS = {"uv_unwrap"} | _NORMALS_PROCESS_KINDS
_FBX_KIND_ALIASES = {"fbx_import", "fbx import", "fbximport"}
_MODELER_KIND_ALIASES = {"modeler"}
_ANIM_RETARGET_KIND_ALIASES = {"anim_retarget", "anim retarget", "animretarget", "retarget"}
_SKINNED_SPLAT_PROXY_KIND_ALIASES = {
    "skinned_splat_proxy",
    "skinned splat proxy",
    "skinnedsplatproxy",
    "fbx_to_skinned_splat_proxy",
    "fbx skinned splat proxy",
}
_GROOM_DEFORM_KIND_ALIASES = {
    "groom_deform",
    "groom deform",
    "groomdeform",
    "hair_deform",
    "hair deform",
}
_FX_SPLAT_PHYSICS_KIND_ALIASES = {
    "fx_splat_physics",
    "fx splat physics",
    "splat_physics",
    "splat physics",
    "splatphysics",
}
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
_FX_MUSIC_EFFECTS_KIND_ALIASES = {
    "fx_music_effects",
    "fx music effects",
    "music_effects",
    "music effects",
    "musiceffects",
}
_SCENE_COLLECT_CACHE_TTL_SEC = 8.0


def _is_anim_retarget_kind(kind: str) -> bool:
    key = str(kind or "").strip().lower()
    return key in _ANIM_RETARGET_KIND_ALIASES or key == "anim_retarget_preview"


def _asset_has_scene_skeleton(asset: dict, source_kind: str = "") -> bool:
    if not isinstance(asset, dict):
        return False
    if bool(asset.get("has_skeleton", False)):
        return True
    if _is_anim_retarget_kind(source_kind or str(asset.get("kind") or "")):
        return True
    context = asset.get("fbx_rig_context")
    if not isinstance(context, dict):
        return False
    if context.get("skeleton") is not None:
        return True
    if bool(context.get("retarget_result", False)):
        return True
    clip = context.get("clip")
    try:
        return clip is not None and bool(getattr(clip, "tracks", None))
    except Exception:
        return clip is not None
_COPY_TO_POINTS_KIND_ALIASES = {
    "copy_to_points",
    "copy to points",
    "copy_to_point",
    "copy to point",
    "copytopoints",
}
_LIGHT_KIND_ALIASES = {
    "light",
    "scene_light",
    "scene light",
    "directional_light",
    "directional light",
    "point_light",
    "point light",
    "spot_light",
    "spot light",
    "area_light",
    "area light",
}
_LIGHT_TYPE_ALIASES = {
    "dir": "directional",
    "directional": "directional",
    "directional_light": "directional",
    "directional light": "directional",
    "point": "point",
    "point_light": "point",
    "point light": "point",
    "spot": "spot",
    "spot_light": "spot",
    "spot light": "spot",
    "spotlight": "spot",
    "area": "area",
    "area_light": "area",
    "area light": "area",
}


def _identity_xform() -> dict:
    return {"pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0], "scl": [1.0, 1.0, 1.0]}


def _is_legacy_default_light_xform(xf) -> bool:
    if not isinstance(xf, dict):
        return False
    try:
        pos = list(xf.get("pos", ()))[:3]
        rot = list(xf.get("rot", ()))[:3]
        scl = list(xf.get("scl", ()))[:3]
        return (
            len(pos) >= 3
            and len(rot) >= 3
            and len(scl) >= 3
            and all(abs(float(a) - float(b)) < 1.0e-4 for a, b in zip(pos, (4.0, 6.0, 4.0)))
            and all(abs(float(a) - float(b)) < 1.0e-4 for a, b in zip(rot, (133.5, 135.0, 0.0)))
            and all(abs(float(a) - float(b)) < 1.0e-4 for a, b in zip(scl, (1.0, 1.0, 1.0)))
        )
    except Exception:
        return False
_MOCAP_KIND_ALIASES = {
    "mocap_import",
    "mocap import",
    "mocapimport",
    "bvh_import",
    "bvh import",
    "bvhimport",
}

_EYE_ICON_CACHE = {}
_FX_LOG_ENABLED = True
_SCENE_LOG_TIMES = {}


def _normalize_light_type(value) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    text = " ".join(text.replace("_", " ").split())
    return _LIGHT_TYPE_ALIASES.get(text, _LIGHT_TYPE_ALIASES.get(text.replace(" ", "_"), "directional"))


def _fx_log(msg: str) -> None:
    if not _FX_LOG_ENABLED:
        return
    try:
        root = Path(__file__).resolve().parents[2]
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / "fx_trail_debug.log").open("a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


def _fx_debug_enabled(model) -> bool:
    return str(_param_value(model, "debug_log") or "").strip().lower() in {"1", "true", "yes", "on"}


def _material_debug_enabled(model) -> bool:
    return str(_param_value(model, "debug_log") or "").strip().lower() in {"1", "true", "yes", "on"}


def _fbx_debug_enabled(model) -> bool:
    return str(_param_value(model, "debug_log") or "").strip().lower() in {"1", "true", "yes", "on"}


def _scene_debug_enabled(model) -> bool:
    return str(_param_value(model, "debug_log") or "").strip().lower() in {"1", "true", "yes", "on"}


def _scene_log(node_item, msg: str, *, key: str | None = None, interval: float = 0.0) -> None:
    model = getattr(node_item, "model", None) if node_item is not None else None
    if not _scene_debug_enabled(model):
        return
    if key and interval > 0.0:
        try:
            now = float(time.time())
            last = float(_SCENE_LOG_TIMES.get(str(key), 0.0) or 0.0)
            if (now - last) < float(interval):
                return
            _SCENE_LOG_TIMES[str(key)] = now
        except Exception:
            pass
    try:
        root = Path(__file__).resolve().parents[2]
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        node_name = str(getattr(model, "name", "") or "").strip() if model is not None else ""
        with (log_dir / "scene_view_debug.log").open("a", encoding="utf-8") as f:
            f.write(f"{ts} [{node_name or 'scene'}] {msg}\n")
    except Exception:
        pass


def _freeze_scene_cache_value(value):
    if isinstance(value, dict):
        return tuple(
            (str(key), _freeze_scene_cache_value(val))
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_scene_cache_value(val) for val in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_scene_cache_value(val) for val in value), key=repr))
    if isinstance(value, float):
        return round(float(value), 8)
    if isinstance(value, (str, int, bool, type(None))):
        return value
    return repr(value)


def _scene_collect_cache_key(node_item):
    scene = node_item.scene() if node_item is not None else None
    model = getattr(node_item, "model", None)
    return (
        int(getattr(scene, "_scene_asset_revision", 0) or 0) if scene is not None else 0,
        int(getattr(scene, "_edge_index_version", 0) or 0) if scene is not None else 0,
        int(_scene_timeline_frame(node_item)),
        _freeze_scene_cache_value(getattr(model, "_scene_hidden", None)),
        _freeze_scene_cache_value(getattr(model, "_scene_xforms", None)),
        _freeze_scene_cache_value(getattr(model, "_scene_retimes", None)),
    )


def _clone_scene_cache_value(value):
    if isinstance(value, dict):
        return {key: _clone_scene_cache_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_clone_scene_cache_value(val) for val in value]
    if isinstance(value, tuple):
        return tuple(_clone_scene_cache_value(val) for val in value)
    if isinstance(value, set):
        return {_clone_scene_cache_value(val) for val in value}
    return value


def _scene_collect_cache_get(node_item):
    entry = getattr(node_item, "_scene_collect_assets_cache", None)
    if not isinstance(entry, tuple) or len(entry) not in {2, 3}:
        return None
    if len(entry) == 3:
        stamp, key, assets = entry
        try:
            if (time.monotonic() - float(stamp)) > float(_SCENE_COLLECT_CACHE_TTL_SEC):
                return None
        except Exception:
            return None
    else:
        key, assets = entry
    if key != _scene_collect_cache_key(node_item):
        return None
    return [_clone_scene_cache_value(asset) for asset in list(assets or [])]


def _scene_collect_cache_put(node_item, assets) -> None:
    try:
        cached_assets = tuple(_clone_scene_cache_value(asset) for asset in list(assets or []))
        node_item._scene_collect_assets_cache = (time.monotonic(), _scene_collect_cache_key(node_item), cached_assets)
    except Exception:
        return

    def _clear_if_same(item=node_item, cached=cached_assets):
        try:
            current = getattr(item, "_scene_collect_assets_cache", None)
            if isinstance(current, tuple) and len(current) == 3 and current[2] is cached:
                item._scene_collect_assets_cache = None
        except Exception:
            pass

    try:
        QtCore.QTimer.singleShot(int(max(1.0, _SCENE_COLLECT_CACHE_TTL_SEC) * 1000.0), _clear_if_same)
    except Exception:
        pass


def _scene_timeline_frame(node_item) -> int:
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return 0
    try:
        views = scene.views()
        if not views:
            return 0
        win = views[0].window()
    except Exception:
        return 0
    glv = getattr(win, "gl_view", None) if win is not None else None
    if glv is None:
        return 0
    fn = getattr(glv, "_timeline_current_frame", None)
    if not callable(fn):
        return 0
    try:
        return max(0, int(fn()))
    except Exception:
        return 0


def _eye_icon(visible: bool) -> QtGui.QIcon:
    key = "on" if visible else "off"
    icon = _EYE_ICON_CACHE.get(key)
    if icon is not None:
        return icon
    size = 14
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    base_color = QtGui.QColor("#e2e8f0")
    pen = QtGui.QPen(base_color)
    pen.setWidthF(1.2)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    rect = QtCore.QRectF(1.6, 4.0, size - 3.2, size - 8.0)
    painter.drawEllipse(rect)
    if visible:
        painter.setBrush(base_color)
        painter.drawEllipse(QtCore.QPointF(size * 0.5, size * 0.5), 2.0, 2.0)
    else:
        hide_pen = QtGui.QPen(QtGui.QColor("#fca5a5"))
        hide_pen.setWidthF(1.4)
        painter.setPen(hide_pen)
        painter.drawLine(3.0, size - 3.0, size - 3.0, 3.0)
    painter.end()
    icon = QtGui.QIcon(pm)
    _EYE_ICON_CACHE[key] = icon
    return icon


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return entry.get("value") or ""
    return ""


def _json_name_list(raw) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, (list, tuple, set)):
        values = parsed
    else:
        values = [part.strip() for part in text.split(",")]
    out: List[str] = []
    seen = set()
    for value in values:
        name = str(value or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = str(_param_value(model, name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _param_float(model, name: str, default: float = 0.0) -> float:
    raw = str(_param_value(model, name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _exclusive_joint_debug_flags(
    show_capture_joints: bool,
    show_animated_joints: bool,
) -> tuple[bool, bool]:
    capture_enabled = bool(show_capture_joints)
    animated_enabled = bool(show_animated_joints)
    if capture_enabled and animated_enabled:
        animated_enabled = False
    return capture_enabled, animated_enabled


def _fbx_import_resolved_rest_path(model) -> str:
    if model is None:
        return ""
    for raw in (
        getattr(model, "_fbx_resolved_rest_geometry", None),
        _param_value(model, "resolved_rest_geometry"),
        _param_value(model, "rest_geometry"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _fbx_import_rig_context(model) -> Dict[str, Any] | None:
    if model is None:
        return None
    bind_result = (
        getattr(model, "_fbx_bind_capture_result", None)
        or getattr(model, "_fbx_bind_rest_result", None)
    )
    skeleton = getattr(bind_result, "skeleton", None) if bind_result is not None else None
    meshes = list(getattr(bind_result, "meshes", []) or []) if bind_result is not None else []
    if skeleton is None:
        return None

    animation_result = (
        getattr(model, "_fbx_anim_animated_result", None)
        or getattr(model, "_fbx_anim_rest_result", None)
    )
    clip = None
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    if clips:
        clip = clips[0]
    weight_debug = _param_bool(
        model,
        "skin_weight_debug",
        default=_param_bool(model, "show_skin_weights", default=False),
    )
    show_capture_joints = _param_bool(
        model,
        "show_capture_joints",
        default=_param_bool(model, "capture_joint_debug", default=False),
    )
    show_animated_joints = _param_bool(
        model,
        "show_animated_joints",
        default=_param_bool(model, "animated_joint_debug", default=False),
    )
    show_capture_joints, show_animated_joints = _exclusive_joint_debug_flags(
        show_capture_joints,
        show_animated_joints,
    )
    return {
        "skeleton": skeleton,
        "clip": clip,
        "meshes": meshes,
        "loop": True,
        "mesh_skinning_enabled": True,
        "skin_weight_debug": bool(weight_debug),
        "show_capture_joints": bool(show_capture_joints),
        "show_animated_joints": bool(show_animated_joints),
        "fbx_debug_log": bool(_fbx_debug_enabled(model)),
    }


def _mocap_import_resolved_path(model) -> str:
    if model is None:
        return ""
    for raw in (
        getattr(model, "_mocap_resolved_path", None),
        _param_value(model, "resolved_path"),
        _param_value(model, "path"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _mocap_import_rig_context(model) -> Dict[str, Any] | None:
    if model is None:
        return None
    path_text = _mocap_import_resolved_path(model)
    if not path_text:
        return None
    try:
        path_obj = Path(path_text)
    except Exception:
        return None
    if path_obj.suffix.lower() != ".bvh":
        return None
    try:
        stat_token = (str(path_obj.resolve()), int(path_obj.stat().st_mtime_ns), int(path_obj.stat().st_size))
    except Exception:
        try:
            stat_token = (str(path_obj), 0, 0)
        except Exception:
            stat_token = ("", 0, 0)

    cached = getattr(model, "_mocap_rig_context_cache", None)
    if isinstance(cached, dict) and cached.get("token") == stat_token:
        context = cached.get("context")
        if isinstance(context, dict):
            return context

    animation_result = getattr(model, "_mocap_animation_result", None)
    try:
        existing_source = str(getattr(animation_result, "source_path", "") or "").strip()
        if existing_source and os.path.normcase(os.path.normpath(existing_source)) != os.path.normcase(os.path.normpath(str(path_obj))):
            animation_result = None
    except Exception:
        animation_result = None
    if animation_result is None:
        try:
            from echograph.rigging.bvh_ingest import ingest_bvh_animation_data

            animation_result = ingest_bvh_animation_data(path_obj)
        except Exception:
            return None
        try:
            setattr(model, "_mocap_animation_result", animation_result)
        except Exception:
            pass

    skeleton = getattr(animation_result, "skeleton", None)
    if skeleton is None:
        return None
    clip = None
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    if clips:
        clip = clips[0]

    context = {
        "skeleton": skeleton,
        "clip": clip,
        "meshes": [],
        "loop": True,
        "mesh_skinning_enabled": False,
        "skin_weight_debug": False,
        "show_capture_joints": False,
        "show_animated_joints": True,
        "fbx_debug_log": bool(_fbx_debug_enabled(model)),
        "source_format": "bvh",
    }
    try:
        setattr(model, "_mocap_rig_context_cache", {"token": stat_token, "context": context})
    except Exception:
        pass
    return context


def _set_param_value(model, name: str, value: str) -> None:
    if model is None:
        return
    key = (name or "").strip().lower()
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
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = value
            return
    params.append({"name": name, "value": value})


def _owner_in_map(mapping, owner: str) -> bool:
    if not isinstance(mapping, dict):
        return False
    key = str(owner or "").strip()
    if not key:
        return False
    if key in mapping:
        return True
    lo = key.lower()
    for k in mapping.keys():
        try:
            if str(k).strip().lower() == lo:
                return True
        except Exception:
            continue
    return False


def _owner_map_get(mapping, owner: str, default=None):
    if not isinstance(mapping, dict):
        return default
    key = str(owner or "").strip()
    if not key:
        return default
    if key in mapping:
        return mapping.get(key, default)
    lo = key.lower()
    for k, v in mapping.items():
        try:
            if str(k).strip().lower() == lo:
                return v
        except Exception:
            continue
    return default


def _clamp01(value, default: float = 0.0) -> float:
    try:
        num = float(value)
    except Exception:
        num = float(default)
    if num > 1.0:
        num *= 0.01
    return max(0.0, min(1.0, num))


def _legacy_refraction_to_ior(value, default: float = 1.50) -> float:
    text = str(value or "").strip()
    if not text:
        return float(default)
    try:
        num = float(text)
    except Exception:
        return float(default)
    if num <= 0.0:
        return 1.0
    if num <= 1.0:
        return max(1.0, min(2.5, 1.0 + num))
    return max(1.0, min(2.5, 1.0 + (num * 0.01)))


def _clamp_ior(value, default: float = 1.50) -> float:
    text = str(value or "").strip()
    if not text:
        return max(1.0, min(2.5, float(default)))
    try:
        num = float(text)
    except Exception:
        return max(1.0, min(2.5, float(default)))
    return max(1.0, min(2.5, num))


def _material_payload(model) -> dict | None:
    if model is None:
        return None
    kind = (getattr(model, "kind", "") or "").strip().lower()
    if kind not in _MATERIAL_KINDS:
        return None
    tint_color = (_param_value(model, "tint_color") or _param_value(model, "base_color") or "#dfe7ff").strip()
    fresnel_color = (_param_value(model, "fresnel_color") or "#ffffff").strip()
    raw_ior = _param_value(model, "ior")
    return {
        "transparency": _clamp01(_param_value(model, "transparency"), 0.8),
        "ior": _clamp_ior(raw_ior, 1.50) if raw_ior.strip() else _legacy_refraction_to_ior(_param_value(model, "refraction"), 1.50),
        "tint_color": tint_color or "#dfe7ff",
        "fresnel_amount": _clamp01(_param_value(model, "fresnel_amount"), 0.0),
        "fresnel_color": fresnel_color or "#ffffff",
    }


def _safe_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return "camera"
    out = []
    for ch in raw:
        if ch.isalnum() or ch in ("_", "-"):
            out.append(ch)
        else:
            out.append("_")
    cleaned = "".join(out).strip("_")
    return cleaned or "camera"


def _camera_proxy_obj_path(node_item, camera_name: str) -> str:
    primitive_spec = None
    try:
        from nodes.primitive import spec as primitive_spec  # type: ignore
    except Exception:
        primitive_spec = None

    base_dir = None
    if primitive_spec is not None:
        get_dir = getattr(primitive_spec, "_primitive_dir", None)
        if callable(get_dir):
            try:
                base_dir = Path(get_dir(node_item))
            except Exception:
                base_dir = None
    if base_dir is None:
        base_dir = Path(tempfile.gettempdir()) / "EchoGraph" / "primitives"
    out_dir = base_dir / "_scene_camera"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{_safe_name(camera_name)}_camera.obj"

    cube_fn = getattr(primitive_spec, "_cube", None) if primitive_spec is not None else None
    write_fn = getattr(primitive_spec, "_write_obj", None) if primitive_spec is not None else None

    if callable(cube_fn):
        cube_verts, cube_faces = cube_fn(size=0.6)
        # Camera body should read as a rectangular block (narrower on X/Y, slightly shorter on Z).
        cube_verts = [(float(x) * 0.57, float(y) * 0.72, float(z) * 1.45) for (x, y, z) in cube_verts]
    else:
        sx = 0.171
        sy = 0.216
        sz = 0.435
        cube_verts = [
            (-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
            (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz),
        ]
        cube_faces = [
            [0, 3, 2, 1], [4, 5, 6, 7], [0, 4, 5, 1],
            [3, 2, 6, 7], [1, 2, 6, 5], [0, 4, 7, 3],
        ]

    # Lens as a tapered tube (frustum): two radii, wide->narrow.
    lens_r_wide = 0.22
    lens_r_narrow = 0.09
    lens_h = 0.325
    segs = 18
    y0 = -0.5 * lens_h
    y1 = 0.5 * lens_h
    cone_verts = []
    for i in range(segs):
        ang = 2.0 * math.pi * (float(i) / float(segs))
        cone_verts.append((lens_r_wide * math.cos(ang), y0, lens_r_wide * math.sin(ang)))
    for i in range(segs):
        ang = 2.0 * math.pi * (float(i) / float(segs))
        cone_verts.append((lens_r_narrow * math.cos(ang), y1, lens_r_narrow * math.sin(ang)))
    cone_faces = []
    for i in range(segs):
        a = i
        b = (i + 1) % segs
        c = segs + ((i + 1) % segs)
        d = segs + i
        cone_faces.append([a, b, c, d])

    verts = [(float(x), float(y), float(z)) for (x, y, z) in cube_verts]
    faces = [[int(i) for i in face] for face in cube_faces if len(face) >= 3]

    try:
        body_min_x = min(float(v[0]) for v in cube_verts)
        body_max_x = max(float(v[0]) for v in cube_verts)
        body_min_y = min(float(v[1]) for v in cube_verts)
        body_max_y = max(float(v[1]) for v in cube_verts)
        body_min_z = min(float(v[2]) for v in cube_verts)
        body_max_z = max(float(v[2]) for v in cube_verts)
    except Exception:
        body_min_x, body_max_x = -0.171, 0.171
        body_min_y, body_max_y = -0.216, 0.216
        body_min_z, body_max_z = -0.435, 0.435

    body_cx = 0.5 * (body_min_x + body_max_x)
    body_cz = 0.5 * (body_min_z + body_max_z)
    body_half_x = max(1e-6, 0.5 * (body_max_x - body_min_x))
    body_half_y = max(1e-6, 0.5 * (body_max_y - body_min_y))
    body_half_z = max(1e-6, 0.5 * (body_max_z - body_min_z))

    top_half_x = body_half_x * 0.62
    top_half_y = body_half_y * 0.24
    top_half_z = body_half_z * 0.82
    top_gap_y = body_half_y * 0.04
    top_cx = body_cx
    top_cy = body_max_y + top_gap_y + top_half_y
    top_cz = body_cz

    top_verts = [
        (top_cx - top_half_x, top_cy - top_half_y, top_cz - top_half_z),
        (top_cx + top_half_x, top_cy - top_half_y, top_cz - top_half_z),
        (top_cx + top_half_x, top_cy + top_half_y, top_cz - top_half_z),
        (top_cx - top_half_x, top_cy + top_half_y, top_cz - top_half_z),
        (top_cx - top_half_x, top_cy - top_half_y, top_cz + top_half_z),
        (top_cx + top_half_x, top_cy - top_half_y, top_cz + top_half_z),
        (top_cx + top_half_x, top_cy + top_half_y, top_cz + top_half_z),
        (top_cx - top_half_x, top_cy + top_half_y, top_cz + top_half_z),
    ]
    top_faces = [
        [0, 3, 2, 1], [4, 5, 6, 7], [0, 4, 5, 1],
        [3, 2, 6, 7], [1, 2, 6, 5], [0, 4, 7, 3],
    ]
    top_offset = len(verts)
    verts.extend(top_verts)
    for face in top_faces:
        faces.append([int(i) + top_offset for i in face])

    body_front_z = 0.0
    try:
        # Place the lens on the opposite face along Z (camera front side).
        body_front_z = min(float(v[2]) for v in cube_verts)
    except Exception:
        body_front_z = 0.0
    cone_base_y = 0.0
    try:
        cone_base_y = min(float(v[1]) for v in cone_verts)
    except Exception:
        cone_base_y = -0.325
    cone_top_y = 0.0
    try:
        cone_top_y = max(float(v[1]) for v in cone_verts)
    except Exception:
        cone_top_y = 0.325
    cone_height = max(0.0, cone_top_y - cone_base_y)
    cone_shift_z = body_front_z - cone_base_y
    # Keep the narrow end flush against the body face, with the frustum extending outward.
    cone_outset_z = -cone_height

    cone_xf = []
    # Rotate cone so its axis points +Z and shift so the base touches
    # the selected front face of the camera body.
    for x, y, z in cone_verts:
        xr = float(x)
        yr = float(z)
        zr = float(y) + cone_shift_z + cone_outset_z
        cone_xf.append((xr, yr, zr))
    offset = len(verts)
    verts.extend(cone_xf)
    for face in cone_faces:
        if len(face) < 3:
            continue
        faces.append([int(i) + offset for i in face])

    try:
        if callable(write_fn):
            write_fn(out_path, verts, faces)
        else:
            lines = ["# EchoGraph scene camera proxy"]
            for x, y, z in verts:
                lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
            for face in faces:
                idxs = " ".join(str(int(i) + 1) for i in face)
                lines.append(f"f {idxs}")
            out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        return ""

    return str(out_path)


def _light_proxy_obj_path(node_item, light_name: str) -> str:
    primitive_spec = None
    try:
        from nodes.primitive import spec as primitive_spec  # type: ignore
    except Exception:
        primitive_spec = None

    base_dir = None
    if primitive_spec is not None:
        get_dir = getattr(primitive_spec, "_primitive_dir", None)
        if callable(get_dir):
            try:
                base_dir = Path(get_dir(node_item))
            except Exception:
                base_dir = None
    if base_dir is None:
        base_dir = Path(tempfile.gettempdir()) / "EchoGraph" / "primitives"
    out_dir = base_dir / "_scene_light"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{_safe_name(light_name)}_light.obj"
    verts = [
        (-0.35, 0.35, 0.0),
        (0.35, 0.35, 0.0),
        (0.35, -0.35, 0.0),
        (-0.35, -0.35, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, -1.05),
        (-0.13, 0.0, -0.82),
        (0.13, 0.0, -0.82),
        (0.0, -0.13, -0.82),
        (0.0, 0.13, -0.82),
    ]
    lines_idx = [
        [0, 1, 2, 3, 0],
        [0, 2],
        [1, 3],
        [4, 5],
        [5, 6],
        [5, 7],
        [5, 8],
        [5, 9],
    ]
    try:
        lines = ["# EchoGraph scene light proxy"]
        for x, y, z in verts:
            lines.append(f"v {float(x):.6f} {float(y):.6f} {float(z):.6f}")
        for row in lines_idx:
            lines.append("l " + " ".join(str(int(i) + 1) for i in row))
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        return ""
    return str(out_path)


def _geometry_process_output_path(item, kind: str, fallback_path: str = "") -> str:
    kind = (kind or "").strip().lower()
    if kind in _NORMALS_PROCESS_KINDS and item is not None:
        try:
            from nodes.normals import spec as _normals_spec  # type: ignore

            build = getattr(_normals_spec, "build_normals_obj", None)
            if callable(build):
                path, _err = build(item, force=False, notify_scene=False)
                if path:
                    return str(path)
        except Exception:
            pass
    return str(fallback_path or "")


def _resolve_input_item(scene, node_item, port_names=None):
    if scene is None or node_item is None:
        return None, "", ""

    def _switch_active_edge(item):
        try:
            edges = list(scene._ordered_in_edges(item))
        except Exception:
            try:
                edges = list(scene._in_edges(item))
            except Exception:
                edges = []
        if not edges:
            return None
        if len(edges) == 1:
            return edges[0]

        model = getattr(item, "model", None)
        by_name = {}
        for edge in edges:
            src = getattr(edge, "src", None)
            src_model = getattr(src, "model", None)
            src_name = (getattr(src_model, "name", "") or "").strip()
            if src_name and src_name not in by_name:
                by_name[src_name] = edge

        ordered = []
        for name in (getattr(model, "switch_inputs", None) or []):
            edge = by_name.get(str(name))
            if edge is not None:
                ordered.append(edge)
        for edge in edges:
            if edge not in ordered:
                ordered.append(edge)
        if not ordered:
            return edges[0]

        try:
            idx = int(getattr(model, "switch_index", 0) or 0)
        except Exception:
            idx = 0
        idx = max(0, min(idx, len(ordered) - 1))
        return ordered[idx]

    def _trace(item, depth=0, visited=None):
        if item is None or depth > 8:
            return None, "", ""
        if visited is None:
            visited = set()
        if item in visited:
            return None, "", ""
        visited.add(item)
        m = getattr(item, "model", None)
        if m is None:
            return None, "", ""
        kind = (getattr(m, "kind", "") or "").strip().lower()
        if kind == "switch":
            edge = _switch_active_edge(item)
            if edge is not None:
                return _trace(getattr(edge, "src", None), depth + 1, visited)
        if (
            kind in {"fx", "fx_trail"}
            or kind in _FX_SPLAT_PHYSICS_KIND_ALIASES
            or kind in _FX_SPLAT_FX_KIND_ALIASES
            or kind in _SPLAT_COLORIZE_KIND_ALIASES
            or kind in _FX_MUSIC_EFFECTS_KIND_ALIASES
        ):
            try:
                edges = list(scene._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(scene._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                chosen = None
                for edge in edges:
                    name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                    if (name or "").strip().lower() in {"mesh", "path", "source", "splats", "splat"}:
                        chosen = edge
                        break
                if chosen is None:
                    for edge in edges:
                        name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                        edge_name = (name or "").strip().lower()
                        if edge_name != "instance":
                            chosen = edge
                            break
                if chosen is None:
                    chosen = edges[0]
                return _trace(getattr(chosen, "src", None), depth + 1, visited)
        if kind in _MODELER_KIND_ALIASES:
            path = _param_value(m, "path")
            try:
                edges = list(scene._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(scene._in_edges(item))
                except Exception:
                    edges = []
            chosen = None
            for edge in edges:
                name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                if (name or "").strip().lower() in {"mesh", "path", "source"}:
                    chosen = edge
                    break
            if chosen is None and edges:
                chosen = edges[0]
            if chosen is not None:
                _up_item, _up_kind, up_path = _trace(getattr(chosen, "src", None), depth + 1, visited)
                if up_path:
                    path = up_path
            return item, kind, path
        if kind in _FBX_KIND_ALIASES:
            return item, kind, _fbx_import_resolved_rest_path(m)
        path = _param_value(m, "path")
        if not path:
            path = _param_value(m, "mesh") or _param_value(m, "source")
        if kind in _GEOMETRY_PROCESS_KINDS:
            path = _geometry_process_output_path(item, kind, path)
        return item, kind, path

    try:
        in_edges = list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            in_edges = list(scene._in_edges(node_item))
        except Exception:
            in_edges = []
    chosen = None
    if port_names:
        wanted = {str(n).strip().lower() for n in port_names if str(n).strip()}
        allow_default_mesh_fallback = bool(wanted) and wanted.issubset({"mesh", "path", "source"})
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in wanted:
                chosen = edge
                break
        if chosen is None and not allow_default_mesh_fallback:
            return None, "", ""
    if chosen is None:
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in {"mesh", "path"}:
                chosen = edge
                break
    if chosen is None:
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if not (name or "").strip():
                chosen = edge
                break
    if chosen is None and in_edges:
        chosen = in_edges[0]
    if chosen is not None:
        src_item = getattr(chosen, "src", None)
        return _trace(src_item, 0, set())
    return None, "", ""


def _fx_target_owner_aliases(scene, owner_item, owner_model, owner_kind):
    aliases = []
    seen = set()

    def _add(name):
        text = str(name or "").strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        aliases.append(text)

    _add(getattr(owner_model, "name", "") if owner_model is not None else "")
    if (owner_kind or "").strip().lower() == "transforms" and owner_item is not None:
        try:
            base_item, _base_kind, _base_path = _resolve_input_item(scene, owner_item)
        except Exception:
            base_item = None
        base_model = getattr(base_item, "model", None) if base_item is not None else None
        _add(getattr(base_model, "name", "") if base_model is not None else "")
    return aliases


def _fx_surface_context(scene, owner_item, owner_model, owner_kind):
    texture_model = owner_model
    texture_kind = (owner_kind or "").strip().lower()
    material_item = None
    material_model = None
    item = owner_item
    model = owner_model
    kind = texture_kind
    visited = set()
    depth = 0
    while item is not None and model is not None and item not in visited and depth < 8:
        visited.add(item)
        depth += 1
        if material_model is None and kind in _MATERIAL_KINDS:
            material_item = item
            material_model = model
        if kind in _TEXTURE_KINDS:
            texture_model = model
            texture_kind = kind
        if kind not in _TEXTURE_KINDS and kind not in _MATERIAL_KINDS and kind not in ({"transforms"} | _GEOMETRY_PROCESS_KINDS):
            break
        next_item, next_kind, _ = _resolve_input_item(scene, item)
        if next_item is None or next_item is item:
            break
        item = next_item
        model = getattr(item, "model", None)
        kind = (next_kind or getattr(model, "kind", "") or "").strip().lower()
    return material_item, material_model, texture_model, texture_kind


@profiled("scene.collect_assets")
def _collect_assets(node_item) -> List[Dict[str, str]]:
    scene = node_item.scene()
    if scene is None:
        return []
    cached_assets = _scene_collect_cache_get(node_item)
    if cached_assets is not None:
        if _scene_debug_enabled(getattr(node_item, "model", None)):
            _scene_log(
                node_item,
                "collect_cache_hit assets=" + str(len(cached_assets)),
                key="scene_collect_cache_hit_" + str(getattr(getattr(node_item, "model", None), "name", "") or "<scene>"),
                interval=0.02,
            )
        return cached_assets
    dbg_collect = _scene_debug_enabled(getattr(node_item, "model", None))

    def _norm_path(p: str) -> str:
        try:
            return os.path.normcase(os.path.normpath(p))
        except Exception:
            return (p or "").strip()

    def _ordered_in_edges(item):
        try:
            return list(scene._ordered_in_edges(item))
        except Exception:
            try:
                return list(scene._in_edges(item))
            except Exception:
                return []

    def _pick_input_edge(item, port_names=None):
        edges = _ordered_in_edges(item)
        if port_names:
            wanted = {str(n).strip().lower() for n in port_names if str(n).strip()}
            for edge in edges:
                name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                if (name or "").strip().lower() in wanted:
                    return edge
        return edges[0] if edges else None

    def _pick_input_edge_strict(item, port_names=None):
        edges = _ordered_in_edges(item)
        wanted = {str(n).strip().lower() for n in (port_names or []) if str(n).strip()}
        if not wanted:
            return None
        for edge in edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in wanted:
                return edge
        return None

    def _parse_vec3(value: str, default):
        try:
            parts = [p.strip() for p in str(value or "").split(",")]
            if len(parts) >= 3:
                return (float(parts[0]), float(parts[1]), float(parts[2]))
        except Exception:
            pass
        return default

    def _parse_int(value, default=1, min_val=1, max_val=200):
        try:
            v = int(float(str(value).strip()))
        except Exception:
            return default
        if v < min_val:
            return min_val
        if v > max_val:
            return max_val
        return v

    def _set_param(model, name: str, value: str) -> None:
        if model is None:
            return
        key = (name or "").strip().lower()
        params = list(getattr(model, "params", None) or [])
        for entry in params:
            if (entry.get("name") or "").strip().lower() == key:
                entry["value"] = str(value or "")
                break
        else:
            params.append({"name": name, "value": str(value or "")})
        try:
            model.params = params
        except Exception:
            pass

    def _find_upstream_transform(start_item):
        item = start_item
        visited = set()
        pass_kinds = {
            "switch",
            "uv_unwrap",
            "normals",
            "normal",
            "smooth_normals",
            "smooth normals",
            "modeler",
            "texture",
            "texture_pro",
            "texture_layer",
            "mnaterial",
            "material",
            "split_volume",
            "volume_selector",
            "fx",
            "fx_trail",
            *_FX_SPLAT_PHYSICS_KIND_ALIASES,
            *_FX_SPLAT_FX_KIND_ALIASES,
            *_SPLAT_COLORIZE_KIND_ALIASES,
        }
        depth = 0
        while item is not None and item not in visited and depth < 10:
            visited.add(item)
            depth += 1
            model = getattr(item, "model", None)
            if model is None:
                break
            kind = (getattr(model, "kind", "") or "").strip().lower()
            if kind == "transforms":
                return model
            if kind not in pass_kinds:
                break
            if kind in ("split_volume", "volume_selector"):
                edge = _pick_input_edge(item, {"mesh", "source", "path"})
            else:
                edge = _pick_input_edge(item)
            item = getattr(edge, "src", None) if edge is not None else None
        return None

    def _chain_has_kind(start_item, kinds):
        item = start_item
        visited = set()
        pass_kinds = {
            "switch",
            "uv_unwrap",
            "normals",
            "normal",
            "smooth_normals",
            "smooth normals",
            "modeler",
            "texture",
            "texture_pro",
            "texture_layer",
            "mnaterial",
            "material",
            "split_volume",
            "volume_selector",
            "transforms",
            "fx",
            "fx_trail",
            *_FX_SPLAT_PHYSICS_KIND_ALIASES,
            *_FX_SPLAT_FX_KIND_ALIASES,
            *_SPLAT_COLORIZE_KIND_ALIASES,
        }
        depth = 0
        while item is not None and item not in visited and depth < 12:
            visited.add(item)
            depth += 1
            model = getattr(item, "model", None)
            if model is None:
                break
            kind = (getattr(model, "kind", "") or "").strip().lower()
            if kind in kinds:
                return True
            if kind not in pass_kinds:
                break
            if kind in ("split_volume", "volume_selector"):
                edge = _pick_input_edge(item, {"mesh", "source", "path"})
            else:
                edge = _pick_input_edge(item)
            item = getattr(edge, "src", None) if edge is not None else None
        return False

    def _lookup_xform(xforms_map, name: str):
        if not name:
            return None
        try:
            if name in xforms_map:
                return xforms_map.get(name)
            nl = name.lower()
            for k, v in xforms_map.items():
                if str(k).strip().lower() == nl:
                    return v
        except Exception:
            return None
        return None

    def _resolve_fx_instance_asset(start_item):
        item = start_item
        visited = set()
        depth = 0
        material_model = None
        material_item = None
        transform_model = None
        base_item = None
        base_model = None
        base_kind = ""
        base_path = ""
        pass_kinds = {
            "switch",
            "uv_unwrap",
            "normals",
            "normal",
            "smooth_normals",
            "smooth normals",
            "modeler",
            "texture",
            "texture_pro",
            "texture_layer",
            "mnaterial",
            "material",
            "transforms",
        }

        while item is not None and item not in visited and depth < 12:
            visited.add(item)
            depth += 1
            model = getattr(item, "model", None)
            if model is None:
                break
            kind = (getattr(model, "kind", "") or "").strip().lower()
            if kind in _MATERIAL_KINDS and material_model is None:
                material_model = model
                material_item = item
            if kind == "transforms" and transform_model is None:
                transform_model = model
            path = _param_value(model, "path") or _param_value(model, "mesh") or _param_value(model, "source")
            if kind not in pass_kinds:
                base_item = item
                base_model = model
                base_kind = kind
                base_path = path
                break
            edge = _pick_input_edge(item, {"mesh", "path", "source"})
            if edge is None:
                base_item = item
                base_model = model
                base_kind = kind
                base_path = path
                break
            item = getattr(edge, "src", None)

        if not base_path and transform_model is not None:
            base_path = _param_value(transform_model, "source") or _param_value(transform_model, "path")

        material_asset = None
        if material_item is not None:
            try:
                from nodes import material as _material_node  # type: ignore
                builder = getattr(_material_node, "build_material_asset", None)
                if callable(builder):
                    material_asset = builder(material_item)
            except Exception:
                material_asset = None
        if isinstance(material_asset, dict):
            mat_path = str(material_asset.get("path") or "").strip()
            if mat_path:
                base_path = mat_path

        instance_xform = None
        if transform_model is not None:
            pos = _parse_vec3(_param_value(transform_model, "pos"), (0.0, 0.0, 0.0))
            rot = _parse_vec3(_param_value(transform_model, "rot"), (0.0, 0.0, 0.0))
            scl = _parse_vec3(_param_value(transform_model, "scl"), (1.0, 1.0, 1.0))
            instance_xform = {
                "pos": [float(pos[0]), float(pos[1]), float(pos[2])],
                "rot": [float(rot[0]), float(rot[1]), float(rot[2])],
                "scl": [float(scl[0]), float(scl[1]), float(scl[2])],
            }

        source_name = (getattr(base_model, "name", "") or "").strip() if base_model is not None else ""
        if not source_name and base_path:
            source_name = Path(base_path).stem

        return {
            "item": base_item,
            "kind": base_kind,
            "path": str(base_path or "").strip(),
            "source_name": source_name,
            "material": (
                dict(material_asset.get("material") or {})
                if isinstance(material_asset, dict) and isinstance(material_asset.get("material"), dict)
                else _material_payload(material_model)
            ),
            "texture": str(material_asset.get("texture") or "").strip() if isinstance(material_asset, dict) else "",
            "texture_provider": material_asset.get("texture_provider") if isinstance(material_asset, dict) else None,
            "xform": instance_xform,
        }

    hidden = set()
    xforms = {}
    try:
        raw_hidden = getattr(getattr(node_item, "model", None), "_scene_hidden", None)
        if isinstance(raw_hidden, set):
            hidden = raw_hidden
        elif isinstance(raw_hidden, (list, tuple)):
            hidden = {str(x) for x in raw_hidden if x}
    except Exception:
        hidden = set()
    try:
        raw_xforms = getattr(getattr(node_item, "model", None), "_scene_xforms", None)
        if isinstance(raw_xforms, dict):
            xforms = raw_xforms
    except Exception:
        xforms = {}
    retimes = {}
    try:
        raw_retimes = getattr(getattr(node_item, "model", None), "_scene_retimes", None)
        if isinstance(raw_retimes, dict):
            retimes = raw_retimes
    except Exception:
        retimes = {}

    def _lookup_retime(retimes_map, name: str):
        if not name:
            return None
        try:
            if name in retimes_map:
                return retimes_map.get(name)
            nl = name.lower()
            for k, v in retimes_map.items():
                if str(k).strip().lower() == nl:
                    return v
        except Exception:
            return None
        return None

    def _apply_retime(entry, name: str) -> bool:
        if not isinstance(entry, dict):
            return False
        raw = _lookup_retime(retimes, name)
        if raw is None:
            return False
        try:
            pct = float(raw)
        except Exception:
            return False
        if pct <= 0.0:
            pct = 100.0
        pct = max(1.0, min(1000.0, float(pct)))
        entry["retime_percent"] = float(pct)
        entry["speed_percent"] = float(pct)
        return True

    def _resolve_processed_owner_chain(start_item, start_kind):
        item = start_item
        kind = (start_kind or "").strip().lower()
        owner_path = ""
        material_model = None
        texture_model = None
        texture_kind = ""
        visited = set()
        depth = 0

        while item is not None and item not in visited and depth < 12:
            visited.add(item)
            depth += 1
            model = getattr(item, "model", None)
            if model is None:
                break
            kind = (kind or getattr(model, "kind", "") or "").strip().lower()

            if kind in _TEXTURE_KINDS:
                texture_model = model
                texture_kind = kind
                next_item, next_kind, next_path = _resolve_input_item(scene, item)
                if next_item is None or next_item is item:
                    break
                if next_path:
                    owner_path = next_path
                item = next_item
                kind = (next_kind or "").strip().lower()
                continue

            if kind in _MATERIAL_KINDS:
                if material_model is None:
                    material_model = model
                next_item, next_kind, next_path = _resolve_input_item(scene, item)
                if next_item is None or next_item is item:
                    break
                if next_path:
                    owner_path = next_path
                item = next_item
                kind = (next_kind or "").strip().lower()
                continue

            if kind in _GEOMETRY_PROCESS_KINDS or kind == "transforms" or kind in _MODELER_KIND_ALIASES:
                next_item, next_kind, next_path = _resolve_input_item(scene, item)
                if next_item is None or next_item is item:
                    break
                if next_path:
                    owner_path = next_path
                item = next_item
                kind = (next_kind or "").strip().lower()
                continue

            break

        return item, kind, owner_path, material_model, texture_model, texture_kind

    def _build_instance_assets(instance_item, instance_model):
        entries = []
        instance_names = []
        if instance_item is None or instance_model is None:
            return entries, instance_names

        count = _parse_int(_param_value(instance_model, "count") or "1", default=1, min_val=1, max_val=200)
        prefix = (_param_value(instance_model, "prefix") or "").strip()
        base_item, base_kind, base_path = _resolve_input_item(scene, instance_item, {"mesh", "path", "source"})
        base_kind = (base_kind or "").strip().lower()

        def _generated_scene_asset_for_instance():
            if base_item is None or not _is_anim_retarget_kind(base_kind):
                return None
            try:
                from nodes.anim_retarget import spec as _anim_retarget_spec  # type: ignore

                build_asset = getattr(_anim_retarget_spec, "build_anim_retarget_scene_asset", None)
                asset = build_asset(base_item) if callable(build_asset) else None
            except Exception:
                asset = None
            return dict(asset) if isinstance(asset, dict) else None

        generated_asset = _generated_scene_asset_for_instance()
        if generated_asset is not None:
            base_path = str(generated_asset.get("path") or base_path or "").strip()
        if not base_path:
            return entries, instance_names

        _set_param_value(instance_model, "path", base_path)
        if not prefix:
            base_name = ""
            try:
                base_name = str((generated_asset or {}).get("node") or "").strip()
                if not base_name:
                    base_model = getattr(base_item, "model", None) if base_item is not None else None
                    base_name = (getattr(base_model, "name", "") or "").strip()
            except Exception:
                base_name = ""
            if not base_name:
                base_name = "instance"
            prefix = f"instance_{base_name}" if base_name else "instance"
            _set_param_value(instance_model, "prefix", prefix)

        if generated_asset is not None:
            base_xform = generated_asset.get("xform") if isinstance(generated_asset.get("xform"), dict) else None
            for idx in range(int(count)):
                inst_name = f"{prefix}_{idx + 1}"
                xf = _lookup_xform(xforms, inst_name)
                entry = dict(generated_asset)
                if isinstance(generated_asset.get("fbx_rig_context"), dict):
                    entry["fbx_rig_context"] = dict(generated_asset.get("fbx_rig_context") or {})
                if isinstance(generated_asset.get("render_proxy"), dict):
                    entry["render_proxy"] = dict(generated_asset.get("render_proxy") or {})
                entry["node"] = inst_name
                entry["visible"] = inst_name not in hidden
                if isinstance(xf, dict):
                    entry["xform"] = dict(xf)
                elif isinstance(base_xform, dict):
                    entry["xform"] = dict(base_xform)
                else:
                    entry.pop("xform", None)
                entries.append(entry)
                instance_names.append(inst_name)
            return entries, instance_names

        owner_model = getattr(base_item, "model", None)
        owner_kind = (base_kind or getattr(owner_model, "kind", "") or "").strip().lower()
        owner_item = base_item
        material_model = owner_model if owner_kind in _MATERIAL_KINDS else None
        texture_model = owner_model
        texture_kind = owner_kind
        path = base_path

        if owner_kind in _MATERIAL_KINDS:
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, base_item)
            if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
                owner_item = upstream_item
                owner_model = getattr(upstream_item, "model", owner_model)
                owner_kind = upstream_kind or owner_kind
                texture_model = owner_model
                texture_kind = owner_kind
                if upstream_path:
                    path = upstream_path

        if owner_kind in _TEXTURE_KINDS:
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, owner_item)
            if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
                if upstream_kind in _GEOMETRY_PROCESS_KINDS:
                    if upstream_path:
                        path = upstream_path
                    owner2_item, owner2_kind, owner2_path, mat2_model, _tex2_model, _tex2_kind = (
                        _resolve_processed_owner_chain(upstream_item, upstream_kind)
                    )
                    if owner2_item is not None and getattr(owner2_item, "model", None) is not None:
                        owner_item = owner2_item
                        owner_model = getattr(owner2_item, "model", owner_model)
                        owner_kind = owner2_kind or owner_kind
                        if mat2_model is not None and material_model is None:
                            material_model = mat2_model
                        if not path and owner2_path:
                            path = owner2_path
                else:
                    owner_item = upstream_item
                    owner_model = getattr(upstream_item, "model", owner_model)
                    owner_kind = upstream_kind or owner_kind
                    if upstream_path:
                        path = upstream_path
        elif owner_kind in _GEOMETRY_PROCESS_KINDS:
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, owner_item)
            if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
                owner2_item, owner2_kind, owner2_path, mat2_model, tex2_model, tex2_kind = (
                    _resolve_processed_owner_chain(upstream_item, upstream_kind)
                )
                if owner2_item is not None and getattr(owner2_item, "model", None) is not None:
                    owner_item = owner2_item
                    owner_model = getattr(owner2_item, "model", owner_model)
                    owner_kind = owner2_kind or owner_kind
                else:
                    owner_item = upstream_item
                    owner_model = getattr(upstream_item, "model", owner_model)
                    owner_kind = upstream_kind or owner_kind
                if mat2_model is not None and material_model is None:
                    material_model = mat2_model
                if tex2_model is not None:
                    texture_model = tex2_model
                    texture_kind = tex2_kind or texture_kind
                if not path:
                    path = upstream_path or owner2_path or path
        elif owner_kind == "transforms":
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, owner_item)
            if upstream_path:
                path = upstream_path

        if not path:
            return entries, instance_names
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            return entries, instance_names

        texture_provider = None
        if texture_kind == "texture_pro":
            try:
                texture_provider = getattr(texture_model, "_texture_pro_provider", None)
            except Exception:
                texture_provider = None
        elif texture_kind == "texture_layer":
            try:
                texture_provider = getattr(texture_model, "_texture_layer_provider", None)
            except Exception:
                texture_provider = None

        if texture_kind in ("texture", "texture_pro"):
            texture = _param_value(texture_model, "texture")
        elif texture_kind == "texture_layer":
            texture = _param_value(texture_model, "texture") if ext == ".obj" else ""
        else:
            texture = _param_value(owner_model, "texture") if ext == ".obj" else ""

        xform_offset = owner_kind in ("split_volume", "volume_selector")
        splat_zero_pivot = False
        if ext == ".ply":
            try:
                if owner_kind == "ply_sequence" or _chain_has_kind(base_item, {"ply_sequence"}):
                    xform_offset = True
                    splat_zero_pivot = True
            except Exception:
                pass
        if not xform_offset:
            try:
                if _chain_has_kind(base_item, {"split_volume", "volume_selector"}):
                    xform_offset = True
            except Exception:
                pass
        transform_model = _find_upstream_transform(base_item)
        if transform_model is not None and path:
            src_path = _param_value(transform_model, "source")
            norm_path = _norm_path(path)
            if src_path and norm_path == _norm_path(src_path):
                pos = _parse_vec3(_param_value(transform_model, "pos"), (0.0, 0.0, 0.0))
                rot = _parse_vec3(_param_value(transform_model, "rot"), (0.0, 0.0, 0.0))
                scl = _parse_vec3(_param_value(transform_model, "scl"), (1.0, 1.0, 1.0))
                base_xf = {"pos": list(pos), "rot": list(rot), "scl": list(scl)}
            else:
                base_xf = None
                xform_offset = True
        else:
            base_xf = None

        material = _material_payload(material_model)
        material_debug_on = bool(material_model is not None and _material_debug_enabled(material_model))

        for idx in range(int(count)):
            inst_name = f"{prefix}_{idx + 1}"
            xf = _lookup_xform(xforms, inst_name)
            entry = {
                "path": path,
                "texture": texture,
                "node": inst_name,
                "ext": ext,
                "visible": inst_name not in hidden,
                "xform": xf if isinstance(xf, dict) else base_xf,
            }
            if material is not None:
                entry["material"] = dict(material)
            if material_debug_on:
                entry["debug_log"] = True
            if xform_offset:
                entry["xform_offset"] = True
            if splat_zero_pivot:
                entry["splat_zero_pivot"] = True
            if texture_provider is not None:
                entry["texture_provider"] = texture_provider
            entries.append(entry)
            instance_names.append(inst_name)
        return entries, instance_names
    try:
        in_edges = list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            in_edges = list(scene._in_edges(node_item))
        except Exception:
            in_edges = []
    if dbg_collect:
        node_name = str(getattr(getattr(node_item, "model", None), "name", "") or "").strip()
        _scene_log(
            node_item,
            "collect_start frame="
            + str(_scene_timeline_frame(node_item))
            + " node="
            + (node_name or "<scene>")
            + " edges="
            + str(len(in_edges))
            + " hidden="
            + str(len(hidden))
            + " xforms="
            + str(len(xforms)),
            key="scene_collect_start_" + (node_name or "<scene>"),
            interval=0.02,
        )

    assets: List[Dict[str, str]] = []
    seen = set()
    seen_wire = set()
    seen_camera = set()
    seen_light = set()
    scene_owner_names = set()
    for edge in in_edges:
        src_item = getattr(edge, "src", None)
        model = getattr(src_item, "model", None)
        if model is None:
            continue
        name = (getattr(model, "name", "") or "").strip()
        if name:
            scene_owner_names.add(name)

    volume_inputs: Dict[str, str] = {}
    for edge in in_edges:
        src_item = getattr(edge, "src", None)
        model = getattr(src_item, "model", None)
        if model is None:
            continue
        src_name = (getattr(model, "name", "") or "").strip()
        kind = (getattr(model, "kind", "") or "").strip().lower()
        if kind not in ("volume_selector", "split_volume"):
            continue
        vol_item, vol_kind, vol_path = _resolve_input_item(scene, src_item, {"volume", "mask"})
        if not vol_path:
            continue
        vol_model = getattr(vol_item, "model", None) if vol_item is not None else None
        owner_name = (getattr(vol_model, "name", "") or "").strip() or Path(vol_path).name
        if not owner_name:
            continue
        volume_inputs[owner_name] = vol_path
        if owner_name not in scene_owner_names:
            ext = Path(vol_path).suffix.lower()
            if ext in SUPPORTED_EXTS:
                wire_key = (owner_name, vol_path.strip())
                if wire_key not in seen_wire:
                    seen_wire.add(wire_key)
                    assets.append({
                        "path": vol_path,
                        "texture": "",
                        "node": owner_name,
                        "ext": ext,
                        "wire_only": True,
                        "volume": True,
                    })

    for edge in in_edges:
        src_item = getattr(edge, "src", None)
        model = getattr(src_item, "model", None)
        if model is None:
            continue
        src_name = (getattr(model, "name", "") or "").strip()
        kind = (getattr(model, "kind", "") or "").strip().lower()
        material_asset = None
        if _is_anim_retarget_kind(kind):
            try:
                from nodes.anim_retarget import spec as _anim_retarget_spec  # type: ignore

                build_asset = getattr(_anim_retarget_spec, "build_anim_retarget_scene_asset", None)
                asset = build_asset(src_item) if callable(build_asset) else None
            except Exception as exc:
                asset = None
                if _fbx_debug_enabled(model):
                    _scene_log(
                        node_item,
                        f"anim_retarget asset build failed node={src_name or kind} err={exc!r}",
                    )
            if isinstance(asset, dict):
                asset_owner = str(asset.get("node") or src_name or kind).strip()
                asset["kind"] = "anim_retarget"
                saved_xform = _lookup_xform(xforms, asset_owner)
                if not isinstance(saved_xform, dict) and asset_owner != src_name:
                    saved_xform = _lookup_xform(xforms, src_name)
                if isinstance(saved_xform, dict):
                    asset["xform"] = dict(saved_xform)
                asset["visible"] = asset_owner not in hidden
                assets.append(asset)
                path_key = str(asset.get("path") or "").strip()
                if path_key:
                    seen.add(path_key)
                if dbg_collect:
                    _scene_log(
                        node_item,
                        "anim_retarget asset "
                        + f"node={str(asset.get('node') or '')} "
                        + f"path={str(asset.get('path') or '')} "
                        + f"saved_xform={bool(isinstance(saved_xform, dict))} "
                        + f"tracks={len(getattr((asset.get('fbx_rig_context') or {}).get('clip'), 'tracks', []) or [])}",
                    )
            continue
        if kind in _SKINNED_SPLAT_PROXY_KIND_ALIASES:
            try:
                from nodes.skinned_splat_proxy import spec as _proxy_spec  # type: ignore

                build_asset = getattr(_proxy_spec, "build_skinned_splat_proxy_scene_asset", None)
                outcome = build_asset(src_item, generate=False) if callable(build_asset) else None
                asset = getattr(outcome, "asset", None)
            except Exception as exc:
                asset = None
                if _scene_debug_enabled(model):
                    _scene_log(
                        node_item,
                        f"skinned_splat_proxy asset build failed node={src_name or kind} err={exc!r}",
                    )
            if isinstance(asset, dict):
                asset_owner = str(asset.get("node") or src_name or kind).strip()
                saved_xform = _lookup_xform(xforms, asset_owner)
                if not isinstance(saved_xform, dict) and asset_owner != src_name:
                    saved_xform = _lookup_xform(xforms, src_name)
                if isinstance(saved_xform, dict):
                    asset["xform"] = dict(saved_xform)
                asset["visible"] = asset_owner not in hidden
                assets.append(asset)
                path_key = str(asset.get("path") or "").strip()
                if path_key:
                    seen.add(path_key)
                if dbg_collect:
                    proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else {}
                    _scene_log(
                        node_item,
                        "skinned_splat_proxy asset "
                        + f"node={str(asset.get('node') or '')} "
                        + f"path={str(asset.get('path') or '')} "
                        + f"proxy={str(proxy.get('manifest') or proxy.get('status') or '')} "
                        + f"hide_source={bool(proxy.get('hide_source_mesh', False))}",
                    )
            continue
        if kind in _GROOM_DEFORM_KIND_ALIASES:
            try:
                from nodes.groom_deform import spec as _groom_deform_spec  # type: ignore

                build_asset = getattr(_groom_deform_spec, "build_groom_deform_scene_asset", None)
                outcome = build_asset(src_item) if callable(build_asset) else None
                asset = getattr(outcome, "asset", None)
                source_assets = tuple(getattr(outcome, "source_assets", None) or tuple())
            except Exception as exc:
                asset = None
                source_assets = tuple()
                if _scene_debug_enabled(model):
                    _scene_log(
                        node_item,
                        f"groom_deform asset build failed node={src_name or kind} err={exc!r}",
                    )
            if isinstance(asset, dict):
                groom_cfg = asset.get("groom_deform") if isinstance(asset.get("groom_deform"), dict) else {}
                hide_deformer = bool(groom_cfg.get("hide_deformer_geo", True))
                if not hide_deformer:
                    for source_asset in source_assets:
                        if not isinstance(source_asset, dict):
                            continue
                        source_entry = dict(source_asset)
                        source_owner = str(source_entry.get("node") or source_entry.get("owner") or "").strip()
                        if source_owner:
                            saved_source_xform = _lookup_xform(xforms, source_owner)
                            if isinstance(saved_source_xform, dict):
                                source_entry["xform"] = dict(saved_source_xform)
                            source_entry["visible"] = source_owner not in hidden
                        source_path = str(source_entry.get("path") or "").strip()
                        if source_path and source_path in seen:
                            continue
                        assets.append(source_entry)
                        if source_path:
                            seen.add(source_path)
                asset_owner = str(asset.get("node") or src_name or kind).strip()
                saved_xform = _lookup_xform(xforms, asset_owner)
                if not isinstance(saved_xform, dict) and asset_owner != src_name:
                    saved_xform = _lookup_xform(xforms, src_name)
                if isinstance(saved_xform, dict):
                    asset["xform"] = dict(saved_xform)
                asset["visible"] = asset_owner not in hidden
                assets.append(asset)
                path_key = str(asset.get("guides_path") or asset.get("path") or "").strip()
                if path_key:
                    seen.add(path_key)
                if dbg_collect:
                    debug = asset.get("debug") if isinstance(asset.get("debug"), dict) else {}
                    _scene_log(
                        node_item,
                        "groom_deform asset "
                        + f"node={str(asset.get('node') or '')} "
                        + f"guides={str(asset.get('guides_path') or '')} "
                        + f"guides_count={int(asset.get('guide_count', 0) or 0)} "
                        + f"bound={int(debug.get('bound_guides', 0) or 0)} "
                        + f"hide_deformer={bool(hide_deformer)}",
                    )
            continue
        if kind in _FX_SPLAT_PHYSICS_KIND_ALIASES:
            try:
                from nodes.fx import splat_physics_spec as _splat_fx_spec  # type: ignore

                build_asset = getattr(_splat_fx_spec, "build_splat_physics_scene_asset", None)
                outcome = build_asset(src_item) if callable(build_asset) else None
                asset = getattr(outcome, "asset", None)
            except Exception as exc:
                asset = None
                if _scene_debug_enabled(model):
                    _scene_log(
                        node_item,
                        f"fx_splat_physics asset build failed node={src_name or kind} err={exc!r}",
                    )
            if isinstance(asset, dict):
                asset_owner = str(asset.get("node") or src_name or kind).strip()
                saved_xform = _lookup_xform(xforms, asset_owner)
                if not isinstance(saved_xform, dict) and asset_owner != src_name:
                    saved_xform = _lookup_xform(xforms, src_name)
                if isinstance(saved_xform, dict):
                    asset["xform"] = dict(saved_xform)
                asset["visible"] = asset_owner not in hidden
                assets.append(asset)
                path_key = str(asset.get("path") or "").strip()
                if path_key:
                    seen.add(path_key)
                if dbg_collect:
                    proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else {}
                    physics = proxy.get("splat_physics") if isinstance(proxy.get("splat_physics"), dict) else {}
                    _scene_log(
                        node_item,
                        "fx_splat_physics asset "
                        + f"node={str(asset.get('node') or '')} "
                        + f"path={str(asset.get('path') or '')} "
                        + f"enabled={bool(physics.get('enabled', True))} "
                        + f"follow={float(physics.get('follow_strength', 0.0) or 0.0):.3f} "
                        + f"drag={float(physics.get('drag', 0.0) or 0.0):.3f}",
                    )
            continue
        if kind in _FX_SPLAT_FX_KIND_ALIASES:
            try:
                from nodes.fx import splat_fx_spec as _splat_fx_spec  # type: ignore

                build_asset = getattr(_splat_fx_spec, "build_splat_fx_scene_asset", None)
                outcome = build_asset(src_item) if callable(build_asset) else None
                asset = getattr(outcome, "asset", None)
            except Exception as exc:
                asset = None
                if _scene_debug_enabled(model):
                    _scene_log(
                        node_item,
                        f"fx_splat_fx asset build failed node={src_name or kind} err={exc!r}",
                    )
            if isinstance(asset, dict):
                asset_owner = str(asset.get("node") or src_name or kind).strip()
                saved_xform = _lookup_xform(xforms, asset_owner)
                if not isinstance(saved_xform, dict) and asset_owner != src_name:
                    saved_xform = _lookup_xform(xforms, src_name)
                if isinstance(saved_xform, dict):
                    asset["xform"] = dict(saved_xform)
                asset["visible"] = asset_owner not in hidden
                assets.append(asset)
                path_key = str(asset.get("path") or "").strip()
                if path_key:
                    seen.add(path_key)
                if dbg_collect:
                    proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else {}
                    splat_fx = proxy.get("splat_fx") if isinstance(proxy.get("splat_fx"), dict) else {}
                    glow_cfg = splat_fx.get("glow") if isinstance(splat_fx.get("glow"), dict) else {}
                    _scene_log(
                        node_item,
                        "fx_splat_fx asset "
                        + f"node={str(asset.get('node') or '')} "
                        + f"path={str(asset.get('path') or '')} "
                        + f"audio={str(splat_fx.get('audio_path') or '')} "
                        + f"enabled={bool(splat_fx.get('enabled', True))} "
                        + f"glow={float(glow_cfg.get('intensity', 0.0) or 0.0):.3f}",
                    )
            continue
        if kind in _SPLAT_COLORIZE_KIND_ALIASES:
            try:
                from nodes.fx import splat_colorize_spec as _splat_colorize_spec  # type: ignore

                build_asset = getattr(_splat_colorize_spec, "build_splat_colorize_scene_asset", None)
                outcome = build_asset(src_item) if callable(build_asset) else None
                asset = getattr(outcome, "asset", None)
            except Exception as exc:
                asset = None
                if _scene_debug_enabled(model):
                    _scene_log(
                        node_item,
                        f"colorize asset build failed node={src_name or kind} err={exc!r}",
                    )
            if isinstance(asset, dict):
                asset_owner = str(asset.get("node") or src_name or kind).strip()
                saved_xform = _lookup_xform(xforms, asset_owner)
                if not isinstance(saved_xform, dict) and asset_owner != src_name:
                    saved_xform = _lookup_xform(xforms, src_name)
                if isinstance(saved_xform, dict):
                    asset["xform"] = dict(saved_xform)
                asset["visible"] = asset_owner not in hidden
                assets.append(asset)
                path_key = str(asset.get("path") or "").strip()
                if path_key:
                    seen.add(path_key)
                if dbg_collect:
                    proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else {}
                    colorize = proxy.get("splat_colorize") if isinstance(proxy.get("splat_colorize"), dict) else {}
                    _scene_log(
                        node_item,
                        "colorize asset "
                        + f"node={str(asset.get('node') or '')} "
                        + f"path={str(asset.get('path') or '')} "
                        + f"enabled={bool(colorize.get('enabled', True))} "
                        + f"metric={str(colorize.get('metric') or '')}",
                    )
            continue
        if kind in _COPY_TO_POINTS_KIND_ALIASES:
            try:
                from nodes.copy_to_points import spec as _copy_to_points_spec  # type: ignore

                build_asset = getattr(_copy_to_points_spec, "build_copy_to_points_scene_asset", None)
                outcome = build_asset(src_item) if callable(build_asset) else None
                asset = getattr(outcome, "asset", None)
            except Exception as exc:
                asset = None
                if _scene_debug_enabled(model):
                    _scene_log(
                        node_item,
                        f"copy_to_points asset build failed node={src_name or kind} err={exc!r}",
                    )
            if isinstance(asset, dict):
                asset_owner = str(asset.get("node") or src_name or kind).strip()
                saved_xform = _lookup_xform(xforms, asset_owner)
                if not isinstance(saved_xform, dict) and asset_owner != src_name:
                    saved_xform = _lookup_xform(xforms, src_name)
                if isinstance(saved_xform, dict):
                    asset["xform"] = dict(saved_xform)
                asset["visible"] = asset_owner not in hidden
                assets.append(asset)
                path_key = str(asset.get("path") or "").strip()
                if path_key:
                    seen.add(path_key)
            continue
        if kind in _FX_MUSIC_EFFECTS_KIND_ALIASES:
            try:
                from nodes.fx import music_effects_spec as _music_fx_spec  # type: ignore

                build_asset = getattr(_music_fx_spec, "build_music_effects_scene_asset", None)
                outcome = build_asset(src_item) if callable(build_asset) else None
                asset = getattr(outcome, "asset", None)
            except Exception as exc:
                asset = None
                if _scene_debug_enabled(model):
                    _scene_log(
                        node_item,
                        f"fx_music_effects asset build failed node={src_name or kind} err={exc!r}",
                    )
            if isinstance(asset, dict):
                asset_owner = str(asset.get("node") or src_name or kind).strip()
                saved_xform = _lookup_xform(xforms, asset_owner)
                if not isinstance(saved_xform, dict) and asset_owner != src_name:
                    saved_xform = _lookup_xform(xforms, src_name)
                if isinstance(saved_xform, dict):
                    asset["xform"] = dict(saved_xform)
                asset["visible"] = asset_owner not in hidden
                assets.append(asset)
                path_key = str(asset.get("path") or "").strip()
                if path_key:
                    seen.add(path_key)
                if dbg_collect:
                    music = asset.get("music_effects") if isinstance(asset.get("music_effects"), dict) else {}
                    mesh_cfg = music.get("mesh") if isinstance(music.get("mesh"), dict) else {}
                    _scene_log(
                        node_item,
                        "fx_music_effects asset "
                        + f"node={str(asset.get('node') or '')} "
                        + f"path={str(asset.get('path') or '')} "
                        + f"audio={str(music.get('audio_path') or '')} "
                        + f"enabled={bool(music.get('enabled', True))} "
                        + f"displace={float(mesh_cfg.get('displacement', 0.0) or 0.0):.3f}",
                    )
            continue
        if kind in _MATERIAL_KINDS:
            try:
                from nodes import material as _material_node  # type: ignore
                builder = getattr(_material_node, "build_material_asset", None)
                if callable(builder):
                    material_asset = builder(src_item)
            except Exception as exc:
                material_asset = None
                if _material_debug_enabled(model):
                    _material_debug_log(
                        "scene.collect.material_builder_error",
                        material_node=src_name or kind,
                        error=repr(exc),
                    )
            if material_asset is None and _material_debug_enabled(model):
                _material_debug_log(
                    "scene.collect.material_builder_empty",
                    material_node=src_name or kind,
                )
        material_debug_on = bool(material_asset.get("debug_log")) if isinstance(material_asset, dict) else _material_debug_enabled(model)
        fx_debug_on = _fx_debug_enabled(model)

        if kind in _LIGHT_KIND_ALIASES:
            light_name = (getattr(model, "name", "") or "").strip() or "light"
            if light_name in seen_light:
                continue
            seen_light.add(light_name)
            light_path = _light_proxy_obj_path(node_item, light_name)
            if not light_path:
                continue
            xf = _lookup_xform(xforms, light_name)
            if not isinstance(xf, dict):
                xf = _identity_xform()
            elif _is_legacy_default_light_xform(xf):
                xf = _identity_xform()
            try:
                intensity = float((_param_value(model, "intensity") or "").strip() or 1.0)
            except Exception:
                intensity = 1.0
            light_range = _param_float(model, "range", 0.0)
            shadow_strength = _param_float(model, "shadow_strength", 1.0)
            shadow_range = _param_float(model, "shadow_range", 0.0)
            shadow_fov = _param_float(model, "shadow_fov", 0.0)
            shadow_near = _param_float(model, "shadow_near", 0.0)
            shadow_bias = _param_float(model, "shadow_bias", 0.0)
            shadow_fov_value = 0.0 if float(shadow_fov) <= 0.0 else max(1.0, min(179.0, float(shadow_fov)))
            light_type = _normalize_light_type(_param_value(model, "type") or _param_value(model, "light_type"))
            assets.append(
                {
                    "path": light_path,
                    "texture": "",
                    "node": light_name,
                    "ext": ".obj",
                    "kind": "light",
                    "visible": light_name not in hidden,
                    "xform": xf,
                    "wire_only": True,
                    "volume": True,
                    "light": {
                        "type": light_type,
                        "intensity": max(0.0, float(intensity)),
                        "range": max(0.0, float(light_range)),
                        "shadow_strength": max(0.0, min(1.0, float(shadow_strength))),
                        "shadow_range": max(0.0, float(shadow_range)),
                        "shadow_fov": float(shadow_fov_value),
                        "shadow_near": max(0.0, float(shadow_near)),
                        "shadow_bias": max(0.0, float(shadow_bias)),
                    },
                }
            )
            continue

        if kind == "camera":
            cam_name = (getattr(model, "name", "") or "").strip() or "camera"
            if cam_name in seen_camera:
                continue
            seen_camera.add(cam_name)
            cam_path = _camera_proxy_obj_path(node_item, cam_name)
            if not cam_path:
                continue
            xf = _lookup_xform(xforms, cam_name)
            if not isinstance(xf, dict):
                xf = {
                    "pos": [0.0, 0.0, 0.0],
                    "rot": [0.0, 0.0, 0.0],
                    "scl": [1.0, 1.0, 1.0],
                }
            try:
                fov = float((_param_value(model, "fov") or "").strip() or 60.0)
            except Exception:
                fov = 60.0
            try:
                aspect_width = int(float((_param_value(model, "aspect_width") or "").strip() or 1920.0))
            except Exception:
                aspect_width = 1920
            try:
                aspect_height = int(float((_param_value(model, "aspect_height") or "").strip() or 1080.0))
            except Exception:
                aspect_height = 1080
            if aspect_width <= 0:
                aspect_width = 1920
            if aspect_height <= 0:
                aspect_height = 1080
            assets.append(
                {
                    "path": cam_path,
                    "texture": "",
                    "node": cam_name,
                    "ext": ".obj",
                    "kind": "camera",
                    "visible": cam_name not in hidden,
                    "xform": xf,
                    "wire_only": True,
                    "volume": True,
                    "fov": fov,
                    "aspect_width": int(aspect_width),
                    "aspect_height": int(aspect_height),
                }
            )
            continue

        if kind == "instance":
            inst_entries, _inst_names = _build_instance_assets(src_item, model)
            assets.extend(inst_entries)
            continue

        path = _param_value(model, "path")
        modeler_hidden: List[str] = []
        fbx_rig_source_model = model if kind in _FBX_KIND_ALIASES else None
        mocap_rig_source_model = model if kind in _MOCAP_KIND_ALIASES else None
        if kind in _FBX_KIND_ALIASES:
            fbx_rig_context = _fbx_import_rig_context(model)
        elif kind in _MOCAP_KIND_ALIASES:
            fbx_rig_context = _mocap_import_rig_context(model)
        else:
            fbx_rig_context = None
        fbx_debug_on = _fbx_debug_enabled(model) if kind in (_FBX_KIND_ALIASES | _MOCAP_KIND_ALIASES) else False
        if kind in _FBX_KIND_ALIASES and not path:
            path = _fbx_import_resolved_rest_path(model)
        if kind in _MOCAP_KIND_ALIASES and not path:
            path = _mocap_import_resolved_path(model)
        if kind in _GEOMETRY_PROCESS_KINDS:
            path = _geometry_process_output_path(src_item, kind, path)
        if isinstance(material_asset, dict):
            try:
                material_path = str(material_asset.get("path") or "").strip()
            except Exception:
                material_path = ""
            if material_path:
                path = material_path
        owner_item = src_item
        owner_model = model
        owner_kind = kind
        material_model = model if kind in _MATERIAL_KINDS else None
        texture_model = model
        texture_kind = kind

        if kind in _MODELER_KIND_ALIASES:
            modeler_hidden = _json_name_list(_param_value(model, "hidden_submeshes"))
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, src_item, {"mesh", "path", "source"})
            upstream_model = getattr(upstream_item, "model", None) if upstream_item is not None else None
            if upstream_path:
                path = upstream_path
            if (upstream_kind or "").strip().lower() in _FBX_KIND_ALIASES:
                fbx_rig_source_model = upstream_model
                fbx_rig_context = _fbx_import_rig_context(upstream_model)
            elif (upstream_kind or "").strip().lower() in _MOCAP_KIND_ALIASES:
                mocap_rig_source_model = upstream_model
                fbx_rig_context = _mocap_import_rig_context(upstream_model)

        # If a texture node is in between, use the upstream model for owner/xform,
        # but keep the texture override from the texture node.
        fx_asset = None
        if kind in ("texture", "texture_pro", "texture_layer", "fx", "fx_trail", "mnaterial", "material"):
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, src_item)
            if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
                if kind in _MATERIAL_KINDS:
                    owner_item = upstream_item
                    owner_model = getattr(upstream_item, "model", owner_model)
                    owner_kind = upstream_kind or owner_kind
                    texture_model = owner_model
                    texture_kind = owner_kind
                    if upstream_path:
                        path = upstream_path
                elif upstream_kind in _GEOMETRY_PROCESS_KINDS:
                    if upstream_path:
                        path = upstream_path
                    owner2_item, owner2_kind, owner2_path, mat2_model, _tex2_model, _tex2_kind = (
                        _resolve_processed_owner_chain(upstream_item, upstream_kind)
                    )
                    if owner2_item is not None and getattr(owner2_item, "model", None) is not None:
                        owner_item = owner2_item
                        owner_model = getattr(owner2_item, "model", owner_model)
                        owner_kind = owner2_kind or owner_kind
                        if mat2_model is not None and material_model is None:
                            material_model = mat2_model
                        if not path and owner2_path:
                            path = owner2_path
                elif kind in ("fx", "fx_trail"):
                    owner_item = upstream_item
                    owner_model = getattr(upstream_item, "model", owner_model)
                    owner_kind = upstream_kind or owner_kind
                    if upstream_path:
                        path = upstream_path
                    fx_material_item, fx_material_model, fx_texture_model, fx_texture_kind = _fx_surface_context(
                        scene,
                        owner_item,
                        owner_model,
                        owner_kind,
                    )
                    if fx_material_model is not None:
                        material_model = fx_material_model
                        try:
                            from nodes import material as _material_node  # type: ignore
                            builder = getattr(_material_node, "build_material_asset", None)
                            if callable(builder):
                                material_asset = builder(fx_material_item)
                        except Exception:
                            material_asset = None
                    if fx_texture_model is not None:
                        texture_model = fx_texture_model
                    if fx_texture_kind:
                        texture_kind = fx_texture_kind
                else:
                    owner_item = upstream_item
                    owner_model = getattr(upstream_item, "model", owner_model)
                    owner_kind = upstream_kind or owner_kind
                    if upstream_path:
                        path = upstream_path
            if kind in ("fx", "fx_trail"):
                try:
                    from nodes.fx import spec as _fx_spec  # type: ignore
                    fx_asset = _fx_spec.trail_asset_config_from_model(model)
                    inst_edge = _pick_input_edge_strict(src_item, {"instance"})
                    inst_info = _resolve_fx_instance_asset(getattr(inst_edge, "src", None) if inst_edge is not None else None)
                    inst_path = str((inst_info or {}).get("path") or "").strip()
                    if inst_path:
                        inst_ext = Path(inst_path).suffix.lower()
                        if inst_ext in SUPPORTED_EXTS:
                            fx_asset["instance_path"] = inst_path
                            if (inst_info or {}).get("source_name"):
                                fx_asset["instance_source_name"] = str(inst_info.get("source_name") or "").strip()
                            if isinstance((inst_info or {}).get("material"), dict):
                                fx_asset["instance_material"] = dict(inst_info.get("material") or {})
                            if str((inst_info or {}).get("texture") or "").strip():
                                fx_asset["instance_texture"] = str(inst_info.get("texture") or "").strip()
                            if (inst_info or {}).get("texture_provider") is not None:
                                fx_asset["instance_texture_provider"] = inst_info.get("texture_provider")
                            if isinstance((inst_info or {}).get("xform"), dict):
                                fx_asset["instance_xform"] = dict(inst_info.get("xform") or {})
                        else:
                            if fx_debug_on:
                                _fx_log(
                                    f"[scene_spec] instance skip unsupported-ext node={src_name or kind} "
                                    f"path={inst_path!r} ext={inst_ext!r}"
                                )
                except Exception as exc:
                    fx_asset = None
                    if fx_debug_on:
                        _fx_log(f"[scene_spec] config error node={src_name or kind} err={exc!r}")
        if kind in _MATERIAL_KINDS and owner_kind == "instance" and owner_item is not None:
            inst_entries, inst_names = _build_instance_assets(owner_item, owner_model)
            if not inst_entries:
                continue
            material = None
            if isinstance(material_asset, dict) and isinstance(material_asset.get("material"), dict):
                material = dict(material_asset.get("material") or {})
            if material is None:
                material = _material_payload(material_model)
            texture = str(material_asset.get("texture") or "") if isinstance(material_asset, dict) else ""
            texture_provider = material_asset.get("texture_provider") if isinstance(material_asset, dict) else None
            for entry in inst_entries:
                if not isinstance(entry, dict):
                    continue
                inst_name = str(entry.get("node") or "").strip()
                if material is not None:
                    entry["material"] = dict(material)
                    rig_context = entry.get("fbx_rig_context")
                    if isinstance(rig_context, dict):
                        rig_context = dict(rig_context)
                        rig_context["skin_weight_debug"] = False
                        rig_context["show_skin_weights"] = False
                        entry["fbx_rig_context"] = rig_context
                if texture:
                    entry["texture"] = texture
                if texture_provider is not None:
                    entry["texture_provider"] = texture_provider
                if material_debug_on:
                    entry["debug_log"] = True
                if inst_name:
                    entry["visible"] = inst_name not in hidden
                assets.append(entry)
                if material_debug_on:
                    _material_debug_log(
                        "scene.collect.material_instance_entry",
                        material_node=src_name or kind,
                        owner_node=inst_name,
                        path=str(entry.get("path") or ""),
                        ext=str(entry.get("ext") or ""),
                        visible=bool(entry.get("visible", True)),
                        has_retarget_context=bool(isinstance(entry.get("fbx_rig_context"), dict)),
                        has_texture=bool(entry.get("texture")),
                        transparency=float((entry.get("material") or {}).get("transparency", 0.0) or 0.0),
                        ior=float((entry.get("material") or {}).get("ior", 1.0) or 1.0),
                        tint_color=str((entry.get("material") or {}).get("tint_color") or ""),
                        fresnel_amount=float((entry.get("material") or {}).get("fresnel_amount", 0.0) or 0.0),
                        fresnel_color=str((entry.get("material") or {}).get("fresnel_color") or ""),
                    )
            continue
        if kind in ("fx", "fx_trail") and owner_kind == "instance" and owner_item is not None:
            inst_entries, inst_names = _build_instance_assets(owner_item, owner_model)
            if not inst_entries:
                continue
            assets.extend(inst_entries)
            if isinstance(fx_asset, dict):
                base_aliases = []
                seen_aliases = set()
                for alias in [str(getattr(owner_model, "name", "") or "").strip()]:
                    if alias and alias.lower() not in seen_aliases:
                        seen_aliases.add(alias.lower())
                        base_aliases.append(alias)
                for idx, inst_name in enumerate(inst_names):
                    aliases = [inst_name]
                    for alias in base_aliases:
                        if alias.lower() != inst_name.lower():
                            aliases.append(alias)
                    fx_entry = dict(fx_asset)
                    fx_entry.update(
                        {
                            "kind": "fx_trail",
                            "node": f"{(src_name or inst_name)}_fx_{idx + 1}",
                            "instance_path": str(fx_entry.get("instance_path") or "").strip(),
                            "target_owner": inst_name,
                            "target_owner_aliases": aliases,
                            "visible": inst_name not in hidden,
                        }
                    )
                    assets.append(fx_entry)
                    if fx_debug_on:
                        _fx_log(
                            f"[scene_spec] append instance trail node={fx_entry['node']} target_owner={inst_name} "
                            f"source_instance={str(getattr(owner_model, 'name', '') or '').strip() or '<instance>'} "
                            f"enabled={bool(fx_entry.get('enabled', True))} global={bool(fx_entry.get('global_space', False))}"
                        )
            continue
        if kind in _MATERIAL_KINDS and owner_kind in _TEXTURE_KINDS:
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, owner_item)
            if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
                if upstream_kind in _GEOMETRY_PROCESS_KINDS:
                    if upstream_path:
                        path = upstream_path
                    upstream2_item, upstream2_kind, _ = _resolve_input_item(scene, upstream_item)
                    if upstream2_item is not None and getattr(upstream2_item, "model", None) is not None:
                        owner_item = upstream2_item
                        owner_model = getattr(upstream2_item, "model", owner_model)
                        owner_kind = upstream2_kind or owner_kind
                else:
                    owner_item = upstream_item
                    owner_model = getattr(upstream_item, "model", owner_model)
                    owner_kind = upstream_kind or owner_kind
                    if upstream_path:
                        path = upstream_path
        elif kind in _MATERIAL_KINDS and owner_kind in _GEOMETRY_PROCESS_KINDS:
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, owner_item)
            if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
                owner_item = upstream_item
                owner_model = getattr(upstream_item, "model", owner_model)
                owner_kind = upstream_kind or owner_kind
                if upstream_path:
                    path = upstream_path
        elif kind in _GEOMETRY_PROCESS_KINDS:
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, src_item)
            if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
                owner2_item, owner2_kind, owner2_path, mat2_model, tex2_model, tex2_kind = (
                    _resolve_processed_owner_chain(upstream_item, upstream_kind)
                )
                if owner2_item is not None and getattr(owner2_item, "model", None) is not None:
                    owner_item = owner2_item
                    owner_model = getattr(owner2_item, "model", owner_model)
                    owner_kind = owner2_kind or owner_kind
                else:
                    owner_item = upstream_item
                    owner_model = getattr(upstream_item, "model", owner_model)
                    owner_kind = upstream_kind or owner_kind
                if mat2_model is not None and material_model is None:
                    material_model = mat2_model
                if tex2_model is not None:
                    texture_model = tex2_model
                    texture_kind = tex2_kind or texture_kind
                if not path:
                    path = upstream_path or owner2_path or path
        elif kind == "switch":
            # Switch is a pass-through node; use the currently active branch.
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, src_item, {"mesh", "path", "source"})
            if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
                owner_item = upstream_item
                owner_model = getattr(upstream_item, "model", owner_model)
                owner_kind = upstream_kind or owner_kind
                texture_model = owner_model
                texture_kind = owner_kind
            if upstream_path:
                path = upstream_path
        elif kind == "transforms":
            upstream_item, upstream_kind, upstream_path = _resolve_input_item(scene, src_item)
            if upstream_path:
                path = upstream_path
            if (upstream_kind or "").strip().lower() in _FBX_KIND_ALIASES:
                fbx_rig_source_model = (
                    getattr(upstream_item, "model", None) if upstream_item is not None else None
                )
                if fbx_rig_context is None:
                    fbx_rig_context = _fbx_import_rig_context(fbx_rig_source_model)
            if (upstream_kind or "").strip().lower() in _MOCAP_KIND_ALIASES:
                mocap_rig_source_model = (
                    getattr(upstream_item, "model", None) if upstream_item is not None else None
                )
                if fbx_rig_context is None:
                    fbx_rig_context = _mocap_import_rig_context(mocap_rig_source_model)
        elif kind == "wire":
            # Always resolve live source from the current graph so rewiring does not
            # depend on pressing the Wire node "View" button.
            order_mode = _param_value(model, "order") or "id"
            source_path = ""
            try:
                from nodes.wire import spec as _wire_spec  # type: ignore
                resolve_src_fn = getattr(_wire_spec, "_resolve_wire_source", None)
                norm_fn = getattr(_wire_spec, "_normalize_order", None)
                build_fn = getattr(_wire_spec, "_build_wire_obj_from_ply", None)

                if callable(resolve_src_fn):
                    source_path = str(resolve_src_fn(src_item) or "").strip()
                if not source_path:
                    source_path = (
                        _param_value(model, "source")
                        or _param_value(model, "mesh")
                        or path
                    ).strip()
                if not source_path:
                    _up_item, _up_kind, _up_path = _resolve_input_item(scene, src_item, {"mesh", "source", "path"})
                    source_path = (_up_path or "").strip()

                if callable(norm_fn):
                    order_mode = str(norm_fn(order_mode))

                if source_path and callable(build_fn):
                    out_path, _detail = build_fn(source_path, mode=order_mode)
                    if out_path:
                        path = str(out_path).strip()
                        _set_param(model, "source", source_path)
                        _set_param(model, "order", order_mode)
                        _set_param(model, "path", path)
            except Exception:
                pass

        if (owner_kind or "").strip().lower() in _MODELER_KIND_ALIASES:
            modeler_hidden = _json_name_list(_param_value(owner_model, "hidden_submeshes"))
            try:
                upstream_item, upstream_kind, upstream_path = _resolve_input_item(
                    scene,
                    owner_item,
                    {"mesh", "path", "source"},
                )
            except Exception:
                upstream_item, upstream_kind, upstream_path = None, "", ""
            upstream_model = getattr(upstream_item, "model", None) if upstream_item is not None else None
            if upstream_path:
                path = upstream_path
            if (upstream_kind or "").strip().lower() in _FBX_KIND_ALIASES:
                fbx_rig_source_model = upstream_model
                if fbx_rig_context is None:
                    fbx_rig_context = _fbx_import_rig_context(upstream_model)
            elif (upstream_kind or "").strip().lower() in _MOCAP_KIND_ALIASES:
                mocap_rig_source_model = upstream_model
                if fbx_rig_context is None:
                    fbx_rig_context = _mocap_import_rig_context(upstream_model)

        if fbx_rig_source_model is None and (owner_kind or "").strip().lower() in _FBX_KIND_ALIASES:
            fbx_rig_source_model = owner_model
        if mocap_rig_source_model is None and (owner_kind or "").strip().lower() in _MOCAP_KIND_ALIASES:
            mocap_rig_source_model = owner_model
        if fbx_rig_context is None and fbx_rig_source_model is not None:
            fbx_rig_context = _fbx_import_rig_context(fbx_rig_source_model)
        if fbx_rig_context is None and mocap_rig_source_model is not None:
            fbx_rig_context = _mocap_import_rig_context(mocap_rig_source_model)
        if fbx_rig_source_model is not None:
            fbx_debug_on = _fbx_debug_enabled(fbx_rig_source_model)
        if mocap_rig_source_model is not None:
            fbx_debug_on = _fbx_debug_enabled(mocap_rig_source_model)

        if not path:
            if kind in _MATERIAL_KINDS:
                if material_debug_on:
                    _material_debug_log(
                        "scene.collect.material_skip_no_path",
                        material_node=src_name or kind,
                        owner_kind=owner_kind or "",
                    )
            if kind in ("fx", "fx_trail"):
                if fx_debug_on:
                    _fx_log(
                        f"[scene_spec] skip unresolved-path node={src_name or kind} "
                        f"target_owner={(getattr(owner_model, 'name', '') or '').strip() or '<none>'} "
                        f"owner_kind={owner_kind or '<none>'}"
                    )
            continue
        node_name = getattr(owner_model, "name", "") or ""
        if isinstance(material_asset, dict):
            mat_node_name = str(material_asset.get("node") or "").strip()
            if mat_node_name:
                node_name = mat_node_name
        vol_path = volume_inputs.get(node_name)
        if vol_path:
            path = vol_path
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            if kind in _MATERIAL_KINDS:
                if material_debug_on:
                    _material_debug_log(
                        "scene.collect.material_skip_unsupported_ext",
                        material_node=src_name or kind,
                        owner_node=node_name,
                        path=path,
                        ext=ext,
                    )
            if kind in ("fx", "fx_trail"):
                if fx_debug_on:
                    _fx_log(
                        f"[scene_spec] skip unsupported-ext node={src_name or kind} "
                        f"target_owner={node_name or '<none>'} ext={ext!r} path={path!r}"
                    )
            continue
        key = path.strip()
        if modeler_hidden:
            key = key + "::modeler_hidden::" + "|".join(sorted(name.lower() for name in modeler_hidden))
        if key in seen:
            if isinstance(fx_asset, dict) and node_name:
                aliases = _fx_target_owner_aliases(scene, owner_item, owner_model, owner_kind)
                fx_entry = dict(fx_asset)
                fx_entry.update(
                    {
                        "kind": "fx_trail",
                        "node": src_name or f"{node_name}_fx",
                        "instance_path": str(fx_entry.get("instance_path") or "").strip(),
                        "target_owner": node_name,
                        "target_owner_aliases": list(aliases),
                        "visible": node_name not in hidden,
                    }
                )
                assets.append(fx_entry)
                if fx_debug_on:
                    _fx_log(
                        f"[scene_spec] append duplicate-base trail node={fx_entry['node']} "
                        f"target_owner={node_name} path={path!r} enabled={bool(fx_entry.get('enabled', True))} "
                        f"global={bool(fx_entry.get('global_space', False))} "
                        f"samples={int(fx_entry.get('samples', 28) or 28)} frame_step={int(fx_entry.get('frame_step', 1) or 1)} "
                        f"spawn_rate={float(fx_entry.get('spawn_rate', fx_entry.get('frame_step', 1.0)) or 1.0):.3f} "
                        f"substeps={int(fx_entry.get('substeps', 1) or 1)} "
                        f"lifespan={int(fx_entry.get('lifespan', max(1, int(fx_entry.get('samples', 28) or 28) * int(fx_entry.get('frame_step', 1) or 1))) or 1)} "
                        f"repeats={int(fx_entry.get('repeats', 1) or 1)} "
                        f"radius={float(fx_entry.get('radius', 0.35) or 0.35):.4f} aliases={aliases!r}"
                    )
            continue
        seen.add(key)
        texture_provider = None
        if isinstance(material_asset, dict) and material_asset.get("texture_provider") is not None:
            texture_provider = material_asset.get("texture_provider")
        if kind in _MATERIAL_KINDS:
            if texture_provider is None and texture_kind == "texture_pro":
                try:
                    texture_provider = getattr(texture_model, "_texture_pro_provider", None)
                except Exception:
                    texture_provider = None
            elif texture_provider is None and texture_kind == "texture_layer":
                try:
                    texture_provider = getattr(texture_model, "_texture_layer_provider", None)
                except Exception:
                    texture_provider = None
        elif kind == "texture_pro":
            try:
                texture_provider = getattr(model, "_texture_pro_provider", None)
            except Exception:
                texture_provider = None
        elif kind == "texture_layer":
            try:
                texture_provider = getattr(model, "_texture_layer_provider", None)
            except Exception:
                texture_provider = None
        elif kind in _GEOMETRY_PROCESS_KINDS:
            if texture_kind == "texture_pro":
                try:
                    texture_provider = getattr(texture_model, "_texture_pro_provider", None)
                except Exception:
                    texture_provider = None
            elif texture_kind == "texture_layer":
                try:
                    texture_provider = getattr(texture_model, "_texture_layer_provider", None)
                except Exception:
                    texture_provider = None
        elif kind in ("fx", "fx_trail"):
            if isinstance(material_asset, dict) and material_asset.get("texture_provider") is not None:
                texture_provider = material_asset.get("texture_provider")
            elif texture_kind == "texture_pro":
                try:
                    texture_provider = getattr(texture_model, "_texture_pro_provider", None)
                except Exception:
                    texture_provider = None
            elif texture_kind == "texture_layer":
                try:
                    texture_provider = getattr(texture_model, "_texture_layer_provider", None)
                except Exception:
                    texture_provider = None
        if kind in _MATERIAL_KINDS:
            if isinstance(material_asset, dict):
                texture = str(material_asset.get("texture") or "")
            elif texture_kind in ("texture", "texture_pro"):
                texture = _param_value(texture_model, "texture")
            elif texture_kind == "texture_layer":
                texture = _param_value(texture_model, "texture") if ext == ".obj" else ""
            else:
                texture = _param_value(owner_model, "texture") if ext == ".obj" else ""
        elif kind in ("fx", "fx_trail"):
            if isinstance(material_asset, dict):
                texture = str(material_asset.get("texture") or "")
            elif texture_kind in ("texture", "texture_pro"):
                texture = _param_value(texture_model, "texture")
            elif texture_kind == "texture_layer":
                texture = _param_value(texture_model, "texture") if ext == ".obj" else ""
            else:
                texture = _param_value(texture_model, "texture") if ext == ".obj" else ""
        elif kind in ("texture", "texture_pro"):
            texture = _param_value(model, "texture")
        elif kind in _GEOMETRY_PROCESS_KINDS and texture_model is not None:
            if texture_kind in ("texture", "texture_pro"):
                texture = _param_value(texture_model, "texture")
            elif texture_kind == "texture_layer":
                texture = _param_value(texture_model, "texture") if ext == ".obj" else ""
            else:
                texture = _param_value(model, "texture") if ext == ".obj" else ""
        else:
            texture = _param_value(model, "texture") if ext == ".obj" else ""
        wire_only = False
        is_volume = False
        if vol_path:
            wire_only = True
            is_volume = True
            texture_provider = None
            texture = ""
        elif kind == "wire":
            # Wire node outputs polyline OBJ data (v/l). Render it with the wire pass.
            wire_only = True
            texture_provider = None
            texture = ""
        xf = _lookup_xform(xforms, node_name)
        xform_offset = owner_kind in ("split_volume", "volume_selector")
        splat_zero_pivot = False
        if ext == ".ply":
            try:
                if owner_kind == "ply_sequence" or _chain_has_kind(src_item, {"ply_sequence"}):
                    xform_offset = True
                    splat_zero_pivot = True
            except Exception:
                pass
        if not xform_offset:
            try:
                if _chain_has_kind(src_item, {"split_volume", "volume_selector"}):
                    xform_offset = True
            except Exception:
                pass
        transform_model = _find_upstream_transform(src_item)
        if transform_model is not None and path:
            src_path = _param_value(transform_model, "source")
            norm_path = _norm_path(path)
            if src_path and norm_path == _norm_path(src_path):
                if xf is None:
                    pos = _parse_vec3(_param_value(transform_model, "pos"), (0.0, 0.0, 0.0))
                    rot = _parse_vec3(_param_value(transform_model, "rot"), (0.0, 0.0, 0.0))
                    scl = _parse_vec3(_param_value(transform_model, "scl"), (1.0, 1.0, 1.0))
                    xf = {"pos": list(pos), "rot": list(rot), "scl": list(scl)}
            else:
                xform_offset = True
        entry = {
            "path": path,
            "texture": texture,
            "node": node_name,
            "ext": ext,
            "visible": node_name not in hidden,
            "xform": xf if isinstance(xf, dict) else None,
        }
        material = None
        if isinstance(material_asset, dict) and isinstance(material_asset.get("material"), dict):
            material = dict(material_asset.get("material") or {})
        if material is None:
            material = _material_payload(material_model)
        if material is not None:
            entry["material"] = material
        if material_debug_on:
            entry["debug_log"] = True
        if isinstance(fbx_rig_context, dict):
            entry["fbx_rig_context"] = fbx_rig_context
        if bool(fbx_debug_on) and (isinstance(fbx_rig_context, dict) or ext in {".fbx", ".bvh"}):
            entry["fbx_debug_log"] = True
        if xform_offset:
            entry["xform_offset"] = True
        if splat_zero_pivot:
            entry["splat_zero_pivot"] = True
        if wire_only:
            entry["wire_only"] = True
            entry["volume"] = is_volume
        if texture_provider is not None:
            entry["texture_provider"] = texture_provider
        if modeler_hidden:
            entry["hidden_submeshes"] = list(modeler_hidden)
        assets.append(entry)
        if kind in _MATERIAL_KINDS:
            if material_debug_on:
                _material_debug_log(
                    "scene.collect.material_entry",
                    material_node=src_name or kind,
                    owner_node=node_name,
                    path=path,
                    ext=ext,
                    visible=bool(entry.get("visible", True)),
                    has_texture=bool(texture),
                    transparency=float((entry.get("material") or {}).get("transparency", 0.0) or 0.0),
                    ior=float((entry.get("material") or {}).get("ior", 1.0) or 1.0),
                    tint_color=str((entry.get("material") or {}).get("tint_color") or ""),
                    fresnel_amount=float((entry.get("material") or {}).get("fresnel_amount", 0.0) or 0.0),
                    fresnel_color=str((entry.get("material") or {}).get("fresnel_color") or ""),
                    xform_offset=bool(entry.get("xform_offset")),
                )
        if isinstance(fx_asset, dict) and node_name:
            aliases = _fx_target_owner_aliases(scene, owner_item, owner_model, owner_kind)
            fx_entry = dict(fx_asset)
            fx_entry.update(
                {
                    "kind": "fx_trail",
                    "node": src_name or f"{node_name}_fx",
                    "instance_path": str(fx_entry.get("instance_path") or "").strip(),
                    "target_owner": node_name,
                    "target_owner_aliases": list(aliases),
                    "visible": node_name not in hidden,
                }
            )
            assets.append(fx_entry)
            if fx_debug_on:
                _fx_log(
                    f"[scene_spec] append trail node={fx_entry['node']} target_owner={node_name} "
                    f"path={path!r} enabled={bool(fx_entry.get('enabled', True))} "
                    f"global={bool(fx_entry.get('global_space', False))} "
                    f"samples={int(fx_entry.get('samples', 28) or 28)} frame_step={int(fx_entry.get('frame_step', 1) or 1)} "
                    f"spawn_rate={float(fx_entry.get('spawn_rate', fx_entry.get('frame_step', 1.0)) or 1.0):.3f} "
                    f"substeps={int(fx_entry.get('substeps', 1) or 1)} "
                    f"lifespan={int(fx_entry.get('lifespan', max(1, int(fx_entry.get('samples', 28) or 28) * int(fx_entry.get('frame_step', 1) or 1))) or 1)} "
                    f"repeats={int(fx_entry.get('repeats', 1) or 1)} "
                    f"radius={float(fx_entry.get('radius', 0.35) or 0.35):.4f} "
                    f"sides={int(fx_entry.get('sides', 28) or 28)} aliases={aliases!r}"
                )
    if dbg_collect:
        frame_idx = _scene_timeline_frame(node_item)
        splat_rows = []
        for a in assets:
            if not isinstance(a, dict):
                continue
            ext_hint = str(a.get("ext") or "").strip().lower()
            if not ext_hint:
                ext_hint = Path(str(a.get("path") or "").strip()).suffix.lower()
            if ext_hint == ".ply":
                splat_rows.append(a)
        _scene_log(
            node_item,
            "collect_done frame="
            + str(frame_idx)
            + " assets="
            + str(len(assets))
            + " splats="
            + str(len(splat_rows)),
            key="scene_collect_done_" + str(getattr(getattr(node_item, "model", None), "name", "") or "<scene>"),
            interval=0.02,
        )
        for row in splat_rows:
            owner = str(row.get("node") or "").strip()
            path = str(row.get("path") or "").strip()
            xf = row.get("xform") if isinstance(row.get("xform"), dict) else {}
            pos = xf.get("pos", (0.0, 0.0, 0.0)) if isinstance(xf, dict) else (0.0, 0.0, 0.0)
            rot = xf.get("rot", (0.0, 0.0, 0.0)) if isinstance(xf, dict) else (0.0, 0.0, 0.0)
            scl = xf.get("scl", (1.0, 1.0, 1.0)) if isinstance(xf, dict) else (1.0, 1.0, 1.0)
            _scene_log(
                node_item,
                "splat_asset frame="
                + str(frame_idx)
                + " owner="
                + (owner or "<none>")
                + " path="
                + path
                + " pos="
                + str(tuple(pos))
                + " rot="
                + str(tuple(rot))
                + " scl="
                + str(tuple(scl)),
            )
            try:
                win = _resolve_window(node_item)
                glv = getattr(win, "gl_view", None) if win is not None else None
                renderer = getattr(glv, "_mgl_renderer", None) or glv
                live = None
                get_splat = getattr(renderer, "_mgl_get_scene_splat_xform", None) if renderer is not None else None
                get_asset = getattr(renderer, "_mgl_get_scene_asset_xform", None) if renderer is not None else None
                if callable(get_splat):
                    try:
                        live = get_splat(owner)
                    except Exception:
                        live = None
                if not isinstance(live, dict) and callable(get_asset):
                    try:
                        live = get_asset(owner)
                    except Exception:
                        live = None
                if isinstance(live, dict):
                    _scene_log(
                        node_item,
                        "splat_live frame="
                        + str(frame_idx)
                        + " owner="
                        + (owner or "<none>")
                        + " pos="
                        + str(tuple(live.get("pos", (0.0, 0.0, 0.0))))
                        + " rot="
                        + str(tuple(live.get("rot", (0.0, 0.0, 0.0))))
                        + " scl="
                        + str(tuple(live.get("scl", (1.0, 1.0, 1.0)))),
                    )
                try:
                    owner_name = str(getattr(glv, "_timeline_owner_name", "") or "").strip() if glv is not None else ""
                    overrides = getattr(glv, "_timeline_manual_override_owners", None) if glv is not None else None
                    if isinstance(overrides, set):
                        ov_count = len(overrides)
                        ov_has = (owner.lower() in {str(x).strip().lower() for x in overrides}) if owner else False
                    else:
                        ov_count = 0
                        ov_has = False
                    _scene_log(
                        node_item,
                        "timeline_state frame="
                        + str(frame_idx)
                        + " target_owner="
                        + (owner_name or "<none>")
                        + " override_count="
                        + str(ov_count)
                        + " owner_has_override="
                        + ("1" if ov_has else "0"),
                        key="scene_timeline_state_" + (str(getattr(getattr(node_item, "model", None), "name", "") or "<scene>")),
                        interval=0.2,
                    )
                except Exception:
                    pass
            except Exception:
                pass

    for entry in assets:
        if not isinstance(entry, dict):
            continue
        owner_name = str(entry.get("node") or "").strip()
        if owner_name and _apply_retime(entry, owner_name):
            continue
        for alias_key in ("source_owner", "target_owner", "instance_source_name"):
            alias = str(entry.get(alias_key) or "").strip()
            if alias and _apply_retime(entry, alias):
                break

    _scene_collect_cache_put(node_item, assets)
    return assets


def _resolve_window(node_item):
    scene = node_item.scene()
    if scene is not None:
        try:
            views = scene.views()
            if views:
                return views[0].window()
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


def _norm_path_key(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.normpath(text))
    except Exception:
        return text.lower()


def _force_scene_wire_white(win, wire_paths) -> None:
    targets = {_norm_path_key(p) for p in (wire_paths or []) if str(p or "").strip()}
    if not targets or win is None:
        return
    try:
        glv = getattr(win, "gl_view", None)
    except Exception:
        glv = None
    if glv is None:
        return
    renderer = getattr(glv, "_mgl_renderer", None) or glv
    scene_obj = getattr(renderer, "_mgl_scene", None)
    if scene_obj is None:
        return

    items = []
    iter_by_tag = getattr(scene_obj, "iter_by_tag", None)
    if callable(iter_by_tag):
        try:
            items = list(iter_by_tag("scene-wire"))
        except Exception:
            items = []
    if not items:
        try:
            items = [it for it in scene_obj.items() if str(getattr(it, "tag", "") or "") == "scene-wire"]
        except Exception:
            items = []

    changed = False
    for item in items:
        payload = getattr(item, "payload", None)
        if not isinstance(payload, dict):
            continue
        key = _norm_path_key(payload.get("path"))
        if key not in targets:
            continue
        payload["color"] = (1.0, 1.0, 1.0, 1.0)
        changed = True

    if changed:
        try:
            renderer.update()
        except Exception:
            try:
                glv.update()
            except Exception:
                pass


class SceneAssemblyWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")

        view_btn = QtWidgets.QPushButton("View Scene")
        view_btn.clicked.connect(self._on_view_clicked)
        view_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(view_btn, 0)
        layout.addWidget(self._status, 0)

        self._ensure_scene()
        self._update_status()

    def sizeHint(self):
        return QtCore.QSize(200, 54)

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._update_status)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(lambda *_: self._update_status())
            except Exception:
                pass
        self._scene_connected = True

    def _update_status(self, *_):
        total, mesh_count, splat_count, camera_count, light_count = self._quick_status_counts()
        if total <= 0:
            self._status.setText("No 3D assets connected.")
            return
        label = f"{total} connected (mesh {mesh_count}"
        if splat_count:
            label += f", splat {splat_count}"
        if camera_count:
            label += f", camera {camera_count}"
        if light_count:
            label += f", light {light_count}"
        label += ")"
        self._status.setText(label)

    def _quick_status_counts(self):
        scene = self._scene
        if scene is None:
            try:
                scene = self._node_item.scene()
            except Exception:
                scene = None
        if scene is None:
            return 0, 0, 0, 0, 0

        try:
            edges = list(scene._ordered_in_edges(self._node_item))
        except Exception:
            try:
                edges = list(scene._in_edges(self._node_item))
            except Exception:
                edges = []

        seen = set()
        mesh_count = 0
        splat_count = 0
        camera_count = 0
        light_count = 0

        for edge in edges:
            src = getattr(edge, "src", None)
            if src is None:
                continue
            ident = id(src)
            if ident in seen:
                continue
            seen.add(ident)
            model = getattr(src, "model", None)
            kind = (getattr(model, "kind", "") or "").strip().lower()
            path = (
                _param_value(model, "path")
                or _param_value(model, "mesh")
                or _param_value(model, "source")
            )
            ext = Path(str(path or "")).suffix.lower()
            if kind == "camera" or ext == ".camera":
                camera_count += 1
            elif kind in _LIGHT_KIND_ALIASES:
                light_count += 1
            elif (
                kind in {"ply_sequence", "ply sequence"}
                or kind in _SKINNED_SPLAT_PROXY_KIND_ALIASES
                or kind in _FX_SPLAT_PHYSICS_KIND_ALIASES
                or kind in _FX_SPLAT_FX_KIND_ALIASES
                or kind in _SPLAT_COLORIZE_KIND_ALIASES
                or ext == ".ply"
            ):
                splat_count += 1
            else:
                mesh_count += 1

        total = mesh_count + splat_count + camera_count + light_count
        return total, mesh_count, splat_count, camera_count, light_count

    def _on_view_clicked(self):
        assets = _collect_assets(self._node_item)
        if not assets:
            QtWidgets.QMessageBox.information(
                self,
                "Scene",
            "Connect one or more 3D import, primitive, material, volume, UV unwrap, texture, texture layer, texture pro, camera, or light nodes first.",
            )
            return
        try:
            _scene_log(
                self._node_item,
                "view_click frame="
                + str(_scene_timeline_frame(self._node_item))
                + " assets="
                + str(len(assets)),
            )
        except Exception:
            pass
        wire_paths = [
            str(a.get("path") or "").strip()
            for a in assets
            if isinstance(a, dict) and bool(a.get("wire_only")) and not bool(a.get("volume"))
        ]
        # Ensure splats start visible on open (avoid auto-hidden splats)
        try:
            raw_hidden = getattr(self._node_item, "model", None)
            raw_hidden = getattr(raw_hidden, "_scene_hidden", None)
            if isinstance(raw_hidden, set):
                hidden_set = raw_hidden
            elif isinstance(raw_hidden, (list, tuple)):
                hidden_set = {str(x) for x in raw_hidden if x}
            else:
                hidden_set = set()
            changed = False
            for a in assets:
                if str(a.get("ext", "")).lower() == ".ply":
                    name = (a.get("node") or "").strip()
                    if name and name in hidden_set:
                        hidden_set.discard(name)
                        changed = True
                    a["visible"] = True
            if changed:
                try:
                    setattr(self._node_item.model, "_scene_hidden", hidden_set)
                except Exception:
                    pass
        except Exception:
            pass
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            QtWidgets.QMessageBox.warning(
                self,
                "Scene",
                "3D view is not available.",
            )
            return
        try:
            if win is not None:
                try:
                    setattr(win, "_active_scene_node", getattr(self._node_item, "model", None))
                    setattr(win, "_active_scene_preview_context", None)
                    setattr(win, "_opening_scene_assets_from_scene_node", True)
                except Exception:
                    pass
            has_wire_paths = any(
                bool(a.get("wire_only")) and not bool(a.get("volume"))
                for a in assets
                if isinstance(a, dict)
            )
            if has_wire_paths and win is not None:
                glv = getattr(win, "gl_view", None)
                if glv is not None:
                    toggle = getattr(glv, "_mgl_wireframe_toggle", None)
                    if toggle is not None:
                        try:
                            if not bool(toggle.isChecked()):
                                toggle.setChecked(True)
                        except Exception:
                            pass
                    else:
                        on_toggle = getattr(glv, "_on_mgl_wireframe_toggled", None)
                        if callable(on_toggle):
                            try:
                                on_toggle(True)
                            except Exception:
                                pass
        except Exception:
            pass
        try:
            splat_rows = [
                a
                for a in assets
                if isinstance(a, dict) and str(a.get("ext") or "").strip().lower() == ".ply"
            ]
            _scene_log(
                self._node_item,
                "view_dispatch frame="
                + str(_scene_timeline_frame(self._node_item))
                + " splats="
                + str(len(splat_rows))
                + " wires="
                + str(len(wire_paths)),
            )
            for row in splat_rows:
                xf = row.get("xform") if isinstance(row.get("xform"), dict) else {}
                _scene_log(
                    self._node_item,
                    "view_splat owner="
                    + str(row.get("node") or "")
                    + " path="
                    + str(row.get("path") or "")
                    + " xform="
                    + str(xf or {}),
                )
        except Exception:
            pass
        try:
            handler(assets)
        finally:
            if win is not None:
                try:
                    setattr(win, "_opening_scene_assets_from_scene_node", False)
                except Exception:
                    pass
        try:
            _force_scene_wire_white(win, wire_paths)
        except Exception:
            pass


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    if not node:
        return False
    node_debug = _scene_debug_enabled(node)
    try:
        card._scene_outliner_log_enabled = bool(node_debug)
    except Exception:
        pass
    if node_debug:
        print("[SceneSpec] augment_infocard_footer called from:", __file__, flush=True)

    def _footer_debug_enabled() -> bool:
        try:
            return _scene_debug_enabled(getattr(card, "_node_ref", None) or node)
        except Exception:
            return False

    try:
        footer_layout.setContentsMargins(0, 0, 0, 0)
    except Exception:
        pass

    # Reuse existing UI to avoid vertical jitter and reflow chaos
    container = getattr(card, "_scene_footer_container", None)
    if container is not None:
        if footer_layout.count() == 0:
            footer_layout.addWidget(container, 1)

        # keep data current
        sc = getattr(card, "_graph_scene", None)
        try:
            fn = getattr(card, "_scene_outliner_connect", None)
            if callable(fn) and sc is not None:
                fn(sc)
        except Exception:
            pass
        try:
            fn = getattr(card, "_scene_outliner_refresh", None)
            if callable(fn):
                fn(sc)
        except Exception:
            pass

        return True

    # -------- build UI once --------
    container = QtWidgets.QWidget()
    container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    card._scene_footer_container = container

    layout = QtWidgets.QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    footer_layout.addWidget(container, 1)

    try:
        # --- Scene Outliner ---
        title = QtWidgets.QLabel("Scene Outliner")
        title.setStyleSheet("color:#94a3b8;font-size:11px;")
        title.setMinimumWidth(0)
        title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(title)

        outliner = QtWidgets.QListWidget()
        outliner.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        outliner.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        outliner.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        outliner.setMinimumWidth(0)
        outliner.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        outliner.setStyleSheet(
            "QListWidget{background:#0f1216;color:#e2e8f0;border:1px solid #3c4450;border-radius:6px;}"
            "QListWidget::item{padding:2px 6px;}"
            "QListWidget::item:selected{background:#334155;}"
        )
        outliner.setMinimumHeight(220)
        outliner.setMaximumHeight(900)
        layout.addWidget(outliner)
        card._scene_outliner_widget = outliner
        card._scene_outliner_log_enabled = bool(node_debug)
        def _outliner_log(msg: str) -> None:
            if not _footer_debug_enabled():
                return
            try:
                root = Path(__file__).resolve().parents[2]
                log_dir = root / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                with (log_dir / "outliner_debug.log").open("a", encoding="utf-8") as f:
                    f.write(f"{ts} {msg}\n")
            except Exception:
                pass
        def _update_row_highlight(current_item=None) -> None:
            rows = getattr(card, "_scene_outliner_rows", None) or []
            for item, widget in rows:
                try:
                    sel = (item is current_item)
                    widget.setProperty("selected", sel)
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
                    widget.update()
                except Exception:
                    pass
        class _OutlinerViewportFilter(QtCore.QObject):
            def eventFilter(self, obj, event):
                try:
                    et = event.type()
                    if et in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                        pos = event.pos()
                        item = outliner.itemAt(pos)
                        if item is not None:
                            try:
                                outliner.setCurrentItem(item)
                            except Exception:
                                pass
                            _outliner_log(
                                f"viewport_click type={int(et)} row={outliner.row(item)} "
                                f"name={item.data(QtCore.Qt.UserRole)} pos=({pos.x()},{pos.y()})"
                            )
                            if et == QtCore.QEvent.MouseButtonDblClick:
                                try:
                                    w = outliner.itemWidget(item)
                                except Exception:
                                    w = None
                                if w is not None:
                                    edit = w.findChild(QtWidgets.QLineEdit)
                                    if edit is not None:
                                        fn = getattr(edit, "_scene_start_edit", None)
                                        if callable(fn):
                                            fn()
                except Exception as exc:
                    _outliner_log(f"viewport_click err={exc!r}")
                return False
        try:
            vp_filter = _OutlinerViewportFilter(outliner)
            outliner.viewport().installEventFilter(vp_filter)
            outliner._viewport_filter = vp_filter
        except Exception:
            pass

        # --- Render Settings ---
        render_title = QtWidgets.QLabel("Render Settings")
        render_title.setStyleSheet("color:#94a3b8;font-size:11px;")
        render_title.setMinimumWidth(0)
        render_title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(render_title)

        render_panel = QtWidgets.QWidget()
        render_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        render_panel.setStyleSheet("QWidget{background:transparent;border:none;}")

        rp = QtWidgets.QHBoxLayout(render_panel)
        rp.setContentsMargins(6, 4, 6, 4)
        rp.setSpacing(4)

        lab = QtWidgets.QLabel("Depth Test (Splats)")
        lab.setStyleSheet("color:#cbd5e1;")
        lab.setWordWrap(True)
        lab.setMinimumWidth(0)
        lab.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        raw = ""
        try:
            raw = _param_value(node, "splat_depth_test").strip().lower()
        except Exception:
            raw = ""
        if raw:
            depth_on = raw in ("1", "true", "yes", "on")
        else:
            # If no param is stored, default to renderer's effective behavior (depth test ON).
            depth_on = True
            try:
                win = card.window()
                glv = getattr(win, "gl_view", None) if win is not None else None
                raw_gl = str(getattr(glv, "_mgl_splat_depth_test", "") or "").strip().lower() if glv is not None else ""
                if raw_gl:
                    depth_on = raw_gl in ("1", "true", "yes", "on")
            except Exception:
                pass

        chk = QtWidgets.QCheckBox()
        chk.setText("")
        chk.setChecked(depth_on)
        chk.setStyleSheet("QCheckBox{padding:0;margin:0;}")

        box = QtWidgets.QWidget()
        box.setFixedSize(18, 18)
        box.setStyleSheet("QWidget{border:1px solid #3c4450;border-radius:3px;background:transparent;}")

        box_lay = QtWidgets.QHBoxLayout(box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(0)
        box_lay.setAlignment(QtCore.Qt.AlignCenter)
        box_lay.addWidget(chk)

        rp.addWidget(box, 0, QtCore.Qt.AlignLeft)
        rp.addWidget(lab, 1, QtCore.Qt.AlignLeft)
        box.mousePressEvent = lambda e: chk.toggle()
        rp.addStretch(1)

        def _apply_depth(v: bool):
            try:
                params = list(getattr(node, "params", None) or [])
                found = False
                for p in params:
                    if (p.get("name") or "").strip().lower() == "splat_depth_test":
                        p["value"] = "1" if v else "0"
                        found = True
                        break
                if not found:
                    params.append({"name": "splat_depth_test", "value": "1" if v else "0"})
                node.params = params
            except Exception:
                pass

            try:
                win = card.window()
                glv = getattr(win, "gl_view", None) if win is not None else None
                if glv is not None:
                    glv._mgl_splat_depth_test = ("1" if v else "0")
                    glv.update()
            except Exception:
                pass

        chk.toggled.connect(lambda v: QtCore.QTimer.singleShot(0, lambda: _apply_depth(v)))
        layout.addWidget(render_panel)

        # --- Transforms ---
        xform_title = QtWidgets.QLabel("Transforms")
        xform_title.setStyleSheet("color:#94a3b8;font-size:11px;")
        xform_title.setMinimumWidth(0)
        xform_title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(xform_title)

        xform_panel = QtWidgets.QWidget()
        xform_panel.setObjectName("SceneXformPanel")
        xform_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        xform_panel.setMinimumWidth(0)
        xform_panel.setStyleSheet(
            "#SceneXformPanel{background:#0f1216;color:#e2e8f0;border:1px solid #3c4450;border-radius:6px;}"
            "#SceneXformPanel QLabel{color:#e2e8f0;border:0px;padding-right:4px;}"
        )

        fp = QtWidgets.QFormLayout(xform_panel)
        fp.setContentsMargins(6, 6, 6, 6)
        fp.setHorizontalSpacing(6)
        fp.setVerticalSpacing(4)
        fp.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldsStayAtSizeHint)
        fp.setRowWrapPolicy(QtWidgets.QFormLayout.DontWrapRows)

        def _mk_spin():
            sb = QtWidgets.QDoubleSpinBox()
            sb.setDecimals(3)                 # fewer digits = smaller control
            sb.setRange(-1e9, 1e9)
            sb.setSingleStep(0.01)
            sb.setKeyboardTracking(False)

            sb.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            sb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            # keep it compact and stable, no vertical weirdness
            sb.setMinimumHeight(22)
            sb.setFixedWidth(72)              # tweak: try 64, 68, 72
            sb.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

            sb.setStyleSheet(
                "QDoubleSpinBox{background:#12151a;color:#e2e8f0;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
            )
            return sb

        def _xyz_row(default=(0.0, 0.0, 0.0)):
            w = QtWidgets.QWidget()
            w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            l = QtWidgets.QHBoxLayout(w)
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(6)

            a = _mk_spin()
            b = _mk_spin()
            c = _mk_spin()
            a.setValue(float(default[0]))
            b.setValue(float(default[1]))
            c.setValue(float(default[2]))

            # do NOT stretch the spinboxes
            l.addWidget(a)
            l.addWidget(b)
            l.addWidget(c)
            l.addStretch(1)

            return w, (a, b, c)


        pos_w, pos_xyz = _xyz_row((0.0, 0.0, 0.0))
        rot_w, rot_xyz = _xyz_row((0.0, 0.0, 0.0))
        scl_w, scl_xyz = _xyz_row((1.0, 1.0, 1.0))

        label_pos = QtWidgets.QLabel("Position")
        label_rot = QtWidgets.QLabel("Rotation")
        label_scl = QtWidgets.QLabel("Scale")
        labels = [label_pos, label_rot, label_scl]
        max_w = 0
        for lb in labels:
            try:
                w = lb.fontMetrics().horizontalAdvance(lb.text())
            except Exception:
                try:
                    w = lb.sizeHint().width()
                except Exception:
                    w = 0
            if w > max_w:
                max_w = w
        max_w = int(max_w + 12)
        for lb in labels:
            lb.setFixedWidth(max_w)
            lb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        fp.addRow(label_pos, pos_w)
        fp.addRow(label_rot, rot_w)
        fp.addRow(label_scl, scl_w)

        card._xform_panel = xform_panel
        card._xform_pos = pos_xyz
        card._xform_rot = rot_xyz
        card._xform_scl = scl_xyz

        xform_panel.setEnabled(False)
        layout.addWidget(xform_panel)

        # --- Selection -> Transforms, and Transforms -> Viewport ---
        card._scene_selected_owner = None
        card._scene_selected_kind = None
        card._xform_updating = False
        card._scene_outliner_user_selected = False

        def _get_glv():
            win = card.window()
            return getattr(win, "gl_view", None) if win is not None else None

        def _scene_is_active() -> bool:
            try:
                win = card.window()
                active = getattr(win, "_active_scene_node", None) if win is not None else None
                node_ref = getattr(card, "_node_ref", None)
                if active is None or node_ref is None:
                    return True
                return active is node_ref
            except Exception:
                return True

        def _lookup_saved_xform(owner: str):
            try:
                node_ref = getattr(card, "_node_ref", None)
                if node_ref is None:
                    return None
                xforms = getattr(node_ref, "_scene_xforms", None)
                if not isinstance(xforms, dict):
                    return None
                if owner in xforms:
                    return xforms.get(owner)
                lo = str(owner).strip().lower()
                for k, v in xforms.items():
                    try:
                        if str(k).strip().lower() == lo:
                            return v
                    except Exception:
                        continue
            except Exception:
                return None
            return None

        def _set_xyz(spins, xyz):
            try:
                card._xform_updating = True
                for sb, v in zip(spins, xyz):
                    sb.blockSignals(True)
                    sb.setValue(float(v))
                    sb.blockSignals(False)
            finally:
                card._xform_updating = False

        def _menu_icon(name: str, alpha: float = 0.7) -> QtGui.QIcon:
            try:
                icon_path = Path(__file__).resolve().parents[2] / "icons" / name
                if not icon_path.exists():
                    return QtGui.QIcon()
                pm = QtGui.QPixmap(str(icon_path))
                if alpha >= 1.0:
                    return QtGui.QIcon(pm)
                out = QtGui.QPixmap(pm.size())
                out.fill(QtCore.Qt.transparent)
                painter = QtGui.QPainter(out)
                painter.setOpacity(float(alpha))
                painter.drawPixmap(0, 0, pm)
                painter.end()
                return QtGui.QIcon(out)
            except Exception:
                return QtGui.QIcon()

        def _show_xform_menu(kind: str, anchor: QtCore.QPoint):
            if kind == "pos":
                spins = card._xform_pos
                reset_vals = (0.0, 0.0, 0.0)
            elif kind == "rot":
                spins = card._xform_rot
                reset_vals = (0.0, 0.0, 0.0)
            else:
                spins = card._xform_scl
                reset_vals = (0.0, 0.0, 0.0)

            menu = QtWidgets.QMenu(card)
            copy_act = QtWidgets.QAction(_menu_icon("Copy_Icon.png", 0.7), "Copy", menu)
            reset_act = QtWidgets.QAction(_menu_icon("Refresh_Icon.png", 0.7), "Reset", menu)

            def _do_copy():
                try:
                    vals = [float(s.value()) for s in spins]
                    text = f"{vals[0]:.3f}, {vals[1]:.3f}, {vals[2]:.3f}"
                    QtWidgets.QApplication.clipboard().setText(text)
                except Exception:
                    pass

            def _do_reset():
                _set_xyz(spins, reset_vals)
                _apply_xform(kind)

            copy_act.triggered.connect(_do_copy)
            reset_act.triggered.connect(_do_reset)
            menu.addAction(copy_act)
            menu.addAction(reset_act)
            try:
                menu.exec_(anchor)
            except Exception:
                try:
                    menu.exec(anchor)
                except Exception:
                    pass

        def _load_xform_from_view(owner: str):
            if not _scene_is_active():
                xf = _lookup_saved_xform(owner)
                if isinstance(xf, dict):
                    _set_xyz(card._xform_pos, xf.get("pos", (0.0, 0.0, 0.0)))
                    _set_xyz(card._xform_rot, xf.get("rot", (0.0, 0.0, 0.0)))
                    _set_xyz(card._xform_scl, xf.get("scl", (1.0, 1.0, 1.0)))
                return
            glv = _get_glv()
            if glv is None:
                xf = _lookup_saved_xform(owner)
                if isinstance(xf, dict):
                    _set_xyz(card._xform_pos, xf.get("pos", (0.0, 0.0, 0.0)))
                    _set_xyz(card._xform_rot, xf.get("rot", (0.0, 0.0, 0.0)))
                    _set_xyz(card._xform_scl, xf.get("scl", (1.0, 1.0, 1.0)))
                return
            # Prefer splat xform when owner is a splat
            getf = getattr(glv, "_mgl_get_scene_asset_xform", None)
            try:
                splat_map = getattr(glv, "_mgl_scene_splats", None)
                if _owner_in_map(splat_map, owner):
                    getf = getattr(glv, "_mgl_get_scene_splat_xform", getf)
            except Exception:
                pass
            if not callable(getf):
                return
            x = getf(owner)
            _set_xyz(card._xform_pos, x.get("pos", (0.0, 0.0, 0.0)))
            _set_xyz(card._xform_rot, x.get("rot", (0.0, 0.0, 0.0)))
            _set_xyz(card._xform_scl, x.get("scl", (1.0, 1.0, 1.0)))
        # expose for external refresh (e.g., gizmo drag)
        card._scene_xform_refresh = _load_xform_from_view

        def _apply_xform(kind: str):
            if card._xform_updating:
                return
            owner = getattr(card, "_scene_selected_owner", None)
            if not owner:
                return
            glv = _get_glv()
            if glv is None:
                return
            setf = getattr(glv, "_mgl_set_scene_asset_xform", None)
            if not callable(setf):
                return
            before = {}
            try:
                getf = getattr(glv, "_mgl_get_scene_asset_xform", None)
                splat_map = getattr(glv, "_mgl_scene_splats", None)
                if _owner_in_map(splat_map, owner):
                    getf = getattr(glv, "_mgl_get_scene_splat_xform", getf)
                if callable(getf):
                    raw_before = getf(owner) or {}
                    before = {
                        "pos": tuple(raw_before.get("pos", (0.0, 0.0, 0.0))),
                        "rot": tuple(raw_before.get("rot", (0.0, 0.0, 0.0))),
                        "scl": tuple(raw_before.get("scl", (1.0, 1.0, 1.0))),
                    }
            except Exception:
                before = {}

            pos = (card._xform_pos[0].value(), card._xform_pos[1].value(), card._xform_pos[2].value())
            rot = (card._xform_rot[0].value(), card._xform_rot[1].value(), card._xform_rot[2].value())
            scl = (card._xform_scl[0].value(), card._xform_scl[1].value(), card._xform_scl[2].value())

            is_splat = False
            try:
                splat_map = getattr(glv, "_mgl_scene_splats", None)
                if _owner_in_map(splat_map, owner):
                    is_splat = True
            except Exception:
                is_splat = False

            if kind == "pos":
                setf(owner, pos=pos, apply_to_scene_models=not is_splat, use_splat_xform=bool(is_splat))
            elif kind == "rot":
                setf(owner, rot=rot, apply_to_scene_models=not is_splat, use_splat_xform=bool(is_splat))
            elif kind == "scl":
                setf(owner, scl=scl, apply_to_scene_models=not is_splat, use_splat_xform=bool(is_splat))
            else:
                setf(owner, pos=pos, rot=rot, scl=scl, apply_to_scene_models=not is_splat, use_splat_xform=bool(is_splat))

            # Outliner edits are temporary overrides while timeline is idle.
            try:
                mark_override = getattr(glv, "_timeline_mark_manual_override", None)
                if callable(mark_override):
                    mark_override(owner)
            except Exception:
                pass

            try:
                win = card.window()
                scene_node = getattr(card, "_node_ref", None)
                after = {"pos": list(pos), "rot": list(rot), "scl": list(scl)}
                actions.record_scene_xform(win, scene_node, owner, before, after)
            except Exception:
                pass
            # Keep gizmo aligned with edits made via the outliner.
            try:
                win = card.window()
                glv = getattr(win, "gl_view", None) if win is not None else None
                if glv is not None and getattr(glv, "_xform_gizmo_owner", None) == owner:
                    glv._xform_gizmo_pos_locked = False
                    glv._xform_gizmo_pos = pos
                    glv.update()
            except Exception:
                pass
            # If this owner is the camera currently driving the viewport, push the
            # edited outliner transform back into the active view immediately.
            try:
                win = card.window()
                glv = getattr(win, "gl_view", None) if win is not None else None
                if glv is not None:
                    mode = str(getattr(glv, "_camera_select_mode", "default") or "default").strip()
                    if mode and mode.lower() != "default" and mode.lower() == str(owner).strip().lower():
                        sync_view = getattr(glv, "_sync_selected_scene_camera_view", None)
                        if callable(sync_view):
                            sync_view(owner)
            except Exception:
                pass
            # Ask host window to refresh/persist scene xforms when available.
            try:
                win = card.window()
                if win is not None and hasattr(win, "update_scene_asset_xform"):
                    win.update_scene_asset_xform(owner)
            except Exception:
                pass
            # Also persist directly from the outliner values so timeline-driven
            # scene reloads keep the intended manual transform.
            try:
                node_ref = getattr(card, "_node_ref", None)
                if node_ref is not None:
                    xforms = getattr(node_ref, "_scene_xforms", None)
                    if not isinstance(xforms, dict):
                        xforms = {}
                    else:
                        xforms = dict(xforms)
                    xforms[str(owner)] = {
                        "pos": [float(pos[0]), float(pos[1]), float(pos[2])],
                        "rot": [float(rot[0]), float(rot[1]), float(rot[2])],
                        "scl": [float(scl[0]), float(scl[1]), float(scl[2])],
                    }
                    setattr(node_ref, "_scene_xforms", xforms)
            except Exception:
                pass

        def _on_outliner_select():
            it = outliner.currentItem()
            if it is None:
                # Qt can briefly report None during list refresh; don’t clear selection
                return
            try:
                prev = getattr(card, "_scene_outliner_editing", None)
                if isinstance(prev, QtWidgets.QLineEdit) and not prev.isReadOnly():
                    try:
                        fn = getattr(prev, "_scene_finish_edit", None)
                        if callable(fn):
                            fn()
                        else:
                            prev.clearFocus()
                    except Exception:
                        prev.clearFocus()
            except Exception:
                pass


            owner = it.data(QtCore.Qt.UserRole)
            owner = str(owner) if owner else None
            if not owner:
                return
            sel_kind = str(it.data(QtCore.Qt.UserRole + 1) or "").strip().lower()
            if not sel_kind:
                sel_kind = "mesh"
            is_skeleton_row = sel_kind == "skeleton"
            preserve_glv = None
            preserve_view_state = None
            if sel_kind == "camera":
                # Do not force a camera look-through on plain outliner selection when lock is off.
                try:
                    win = card.window()
                    preserve_glv = getattr(win, "gl_view", None) if win is not None else None
                    if preserve_glv is not None and not bool(getattr(preserve_glv, "_camera_select_lock_enabled", False)):
                        renderer = getattr(preserve_glv, "_mgl_renderer", None) or preserve_glv
                        get_state = getattr(renderer, "_mgl_get_camera_state", None)
                        if callable(get_state):
                            st = get_state() or {}
                            if isinstance(st, dict):
                                st = dict(st)
                                st.pop("scene_xforms", None)
                                preserve_view_state = st
                except Exception:
                    preserve_glv = None
                    preserve_view_state = None

            card._scene_selected_owner = owner
            card._scene_selected_kind = sel_kind
            card._scene_outliner_user_selected = True
            try:
                win = card.window()
                ctl = getattr(win, "_timeline_controller", None) if win is not None else None
                if ctl is not None and not bool(is_skeleton_row):
                    glv = getattr(win, "gl_view", None) if win is not None else None
                    apply_current_frame = True
                    if glv is not None and sel_kind == "camera":
                        try:
                            mode = str(getattr(glv, "_camera_select_mode", "default") or "default").strip()
                            camera_live = (
                                bool(getattr(glv, "_camera_select_lock_enabled", False))
                                or bool(getattr(glv, "_fly_mode_enabled", False))
                                or bool(getattr(glv, "_fps_camera_active", False))
                            )
                            if mode and mode.lower() == str(owner).strip().lower() and bool(camera_live):
                                apply_current_frame = False
                        except Exception:
                            apply_current_frame = True
                    try:
                        log_fn = getattr(glv, "_timeline_scene_view_log", None) if glv is not None else None
                        if callable(log_fn):
                            log_fn(
                                "outliner_select "
                                f"owner={str(owner)!r} kind={sel_kind!r} "
                                f"apply_current_frame={bool(apply_current_frame)} "
                                f"camera_mode={str(getattr(glv, '_camera_select_mode', 'default') or 'default') if glv is not None else 'default'!r} "
                                f"fly={bool(getattr(glv, '_fly_mode_enabled', False)) if glv is not None else False} "
                                f"lock={bool(getattr(glv, '_camera_select_lock_enabled', False)) if glv is not None else False}",
                                throttle_key=f"outliner_select:{str(owner).strip()}",
                                interval=0.05,
                            )
                    except Exception:
                        pass
                    try:
                        debug_fn = getattr(glv, "_timeline_camera_key_debug_log", None) if glv is not None else None
                        if callable(debug_fn):
                            debug_fn(
                                "outliner_select",
                                owner=str(owner),
                                frame=glv._timeline_current_frame() if hasattr(glv, "_timeline_current_frame") else None,
                                extra={
                                    "kind": sel_kind,
                                    "apply_current_frame": bool(apply_current_frame),
                                    "camera_mode": str(getattr(glv, "_camera_select_mode", "default") or "default"),
                                    "fly": bool(getattr(glv, "_fly_mode_enabled", False)),
                                    "lock": bool(getattr(glv, "_camera_select_lock_enabled", False)),
                                },
                            )
                    except Exception:
                        pass
                    ctl.sync_timeline_context(
                        apply_current_frame=bool(apply_current_frame),
                        load_audio=bool(apply_current_frame),
                    )
            except Exception:
                pass
            xform_panel.setEnabled(not bool(is_skeleton_row))
            if not bool(is_skeleton_row):
                _load_xform_from_view(owner)
            _update_row_highlight(it)

            if bool(is_skeleton_row):
                try:
                    win = card.window()
                    glv = getattr(win, "gl_view", None) if win is not None else None
                    if glv is not None:
                        renderer = getattr(glv, "_mgl_renderer", None) or glv
                        log_skel = getattr(renderer, "_mgl_scene_skeleton_log", None)
                        if callable(log_skel):
                            log_skel("outliner_skeleton_select", owner=owner)
                        show_skel = getattr(renderer, "_mgl_scene_skeleton_request_active", None)
                        if callable(show_skel):
                            show_skel(owner)
                        try:
                            glv._xform_gizmo_owner = None
                            glv._xform_gizmo_owner_kind = None
                            glv._xform_gizmo_pos_locked = False
                        except Exception:
                            pass
                        try:
                            if hasattr(glv, "set_scene_asset_uv_overlay"):
                                glv.set_scene_asset_uv_overlay(None)
                        except Exception:
                            pass
                        try:
                            glv.update()
                        except Exception:
                            pass
                except Exception:
                    pass
                return

            if not _scene_is_active():
                return

            # tell viewport which owner is selected (for gizmo draw)
            try:
                win = card.window()
                glv = getattr(win, "gl_view", None) if win is not None else None
                if glv is not None:
                    renderer = getattr(glv, "_mgl_renderer", None) or glv
                    show_skel = getattr(renderer, "_mgl_scene_skeleton_request_active", None)
                    if callable(show_skel):
                        show_skel("")
                    glv._xform_gizmo_owner = owner
                    try:
                        refresh_speed = getattr(glv, "_timeline_refresh_speed_control", None)
                        if callable(refresh_speed):
                            refresh_speed()
                    except Exception:
                        pass
                    try:
                        if hasattr(glv, "set_scene_asset_uv_overlay"):
                            glv.set_scene_asset_uv_overlay(owner)
                    except Exception:
                        pass

                    # compute a stable gizmo position:
                    # 1) use stored xform pos if it's non-zero
                    # 2) otherwise use bounds center
                    pos = getattr(glv, "_xform_gizmo_pos", (0.0, 0.0, 0.0))
                    try:
                        renderer = getattr(glv, "_mgl_renderer", None) or glv

                        xf = {}
                        is_splat = False
                        try:
                            splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
                            if not isinstance(splat_map, dict) or not splat_map:
                                splat_map = getattr(renderer, "_mgl_scene_splats", None)
                            if _owner_in_map(splat_map, owner):
                                is_splat = True
                        except Exception:
                            is_splat = False

                        try:
                            glv._xform_gizmo_owner_kind = "splat" if is_splat else "mesh"
                        except Exception:
                            pass

                        get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                        if callable(get_xf):
                            xf = get_xf(owner) or {}

                        xf_pos = tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))
                        if is_splat:
                            pivot = None
                            try:
                                mins = maxs = None
                                bounds_map = (
                                    getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                                    or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
                                )
                                if _owner_in_map(bounds_map, owner):
                                    mins, maxs = _owner_map_get(bounds_map, owner, (None, None)) or (None, None)
                                pivot_fn = getattr(renderer, "_mgl_owner_pivot_local", None)
                                if callable(pivot_fn):
                                    raw_pivot = pivot_fn(owner, mins, maxs) if (mins is not None and maxs is not None) else pivot_fn(owner)
                                    if isinstance(raw_pivot, (list, tuple)) and len(raw_pivot) >= 3:
                                        pivot = (
                                            float(raw_pivot[0]),
                                            float(raw_pivot[1]),
                                            float(raw_pivot[2]),
                                        )
                                if pivot is None and mins is not None and maxs is not None:
                                    pivot = (
                                        (float(mins[0]) + float(maxs[0])) * 0.5,
                                        (float(mins[1]) + float(maxs[1])) * 0.5,
                                        (float(mins[2]) + float(maxs[2])) * 0.5,
                                    )
                            except Exception:
                                pivot = None
                            if pivot is not None:
                                pos = (
                                    float(xf_pos[0] + pivot[0]),
                                    float(xf_pos[1] + pivot[1]),
                                    float(xf_pos[2] + pivot[2]),
                                )
                            else:
                                pos = xf_pos
                        else:
                            # mesh: pos is already world pivot (even if zero)
                            pos = xf_pos
                    except Exception:
                        pass

                    glv._xform_gizmo_pos_locked = False
                    glv._xform_gizmo_pos = pos

                    try:
                        glv.update()
                    except Exception:
                        pass
            except Exception:
                pass

            _load_xform_from_view(owner)
            if preserve_glv is not None and isinstance(preserve_view_state, dict):
                try:
                    renderer = getattr(preserve_glv, "_mgl_renderer", None) or preserve_glv
                    apply_state = getattr(renderer, "_mgl_apply_camera_state", None)
                    if callable(apply_state):
                        apply_state(dict(preserve_view_state))
                    if not bool(getattr(preserve_glv, "_fly_mode_enabled", False)):
                        preserve_glv._fps_camera_active = False
                    preserve_glv.update()
                except Exception:
                    pass


        outliner.currentItemChanged.connect(lambda *_: _on_outliner_select())
        def _on_item_clicked(item):
            if item is None:
                return
            try:
                _outliner_log(f"itemClicked name={item.data(QtCore.Qt.UserRole)}")
            except Exception:
                pass
        outliner.itemClicked.connect(_on_item_clicked)

        for kind, lbl in (("pos", label_pos), ("rot", label_rot), ("scl", label_scl)):
            lbl.setCursor(QtCore.Qt.PointingHandCursor)
            lbl.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            lbl.customContextMenuRequested.connect(
                lambda pt, k=kind, w=lbl: _show_xform_menu(k, w.mapToGlobal(pt))
            )
            def _ctx_ev(ev, k=kind):
                try:
                    _show_xform_menu(k, ev.globalPos())
                    ev.accept()
                except Exception:
                    pass
            lbl.contextMenuEvent = _ctx_ev

        # Push edits on commit
        for sb in card._xform_pos:
            sb.editingFinished.connect(lambda k="pos": _apply_xform(k))
        for sb in card._xform_rot:
            sb.editingFinished.connect(lambda k="rot": _apply_xform(k))
        for sb in card._xform_scl:
            sb.editingFinished.connect(lambda k="scl": _apply_xform(k))


        # ---------- Outliner logic ----------
        def _hidden_set() -> set:
            raw2 = getattr(node, "_scene_hidden", None)
            if isinstance(raw2, set):
                return raw2
            if isinstance(raw2, (list, tuple)):
                out = {str(x) for x in raw2 if x}
                setattr(node, "_scene_hidden", out)
                return out
            out = set()
            setattr(node, "_scene_hidden", out)
            return out

        def _apply_visibility(name: str, visible: bool):
            hidden = _hidden_set()
            if visible:
                hidden.discard(name)
            else:
                hidden.add(name)
            # Log visibility changes for debugging (splat/mesh eye toggle)
            if _footer_debug_enabled():
                try:
                    msg = (
                        "outliner: eye name="
                        + str(name)
                        + " visible="
                        + str(bool(visible))
                        + " hidden_count="
                        + str(len(hidden))
                    )
                    _outliner_log(msg)
                    win = card.window()
                    glv = getattr(win, "gl_view", None) if win is not None else None
                    if glv is not None:
                        glv._mgl_log(msg)
                except Exception:
                    pass
            win = card.window()
            handler = getattr(win, "set_scene_asset_visible", None) if win is not None else None
            if callable(handler):
                handler(name, visible)

        def _apply_rename(scene, old_name: str, new_name: str) -> bool:
            if not new_name or new_name == old_name:
                return False
            ok, msg = scene.rename_node(old_name, new_name)
            if not ok:
                if msg:
                    QtWidgets.QMessageBox.warning(card, "Rename", msg)
                return False
            hidden = _hidden_set()
            if old_name in hidden:
                hidden.remove(old_name)
                hidden.add(new_name)
            win = card.window()
            handler = getattr(win, "rename_scene_asset_owner", None) if win is not None else None
            if callable(handler):
                handler(old_name, new_name)
            return True

        def _quick_outliner_asset_rows(scene, item):
            try:
                edges = list(scene._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(scene._in_edges(item))
                except Exception:
                    edges = []
            rows = []
            seen_names = set()
            for edge in edges:
                src = getattr(edge, "src", None)
                if src is None:
                    continue
                model = getattr(src, "model", None)
                name = str(getattr(model, "name", "") or "").strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                kind = (getattr(model, "kind", "") or "").strip().lower()
                path = (
                    _param_value(model, "path")
                    or _param_value(model, "mesh")
                    or _param_value(model, "source")
                )
                ext = Path(str(path or "")).suffix.lower()
                if kind == "camera" or ext == ".camera":
                    rows.append({"node": name, "path": "", "kind": "camera", "ext": ".camera", "_quick": True})
                elif kind in _LIGHT_KIND_ALIASES:
                    rows.append({"node": name, "path": "", "kind": "light", "ext": "", "_quick": True})
                else:
                    if (
                        kind in {"ply_sequence", "ply sequence"}
                        or kind in _SKINNED_SPLAT_PROXY_KIND_ALIASES
                        or kind in _FX_SPLAT_PHYSICS_KIND_ALIASES
                        or kind in _FX_SPLAT_FX_KIND_ALIASES
                        or kind in _SPLAT_COLORIZE_KIND_ALIASES
                    ) and not ext:
                        ext = ".ply"
                    asset_kind = "anim_retarget" if _is_anim_retarget_kind(kind) else "mesh"
                    rows.append(
                        {
                            "node": name,
                            "path": path,
                            "kind": asset_kind,
                            "ext": ext,
                            "_quick": True,
                            "has_skeleton": bool(_is_anim_retarget_kind(kind)),
                        }
                    )
            return rows

        def _outliner_alive() -> bool:
            try:
                if outliner is None:
                    return False
                if getattr(card, "_scene_outliner_widget", None) is not outliner:
                    return False
                # Touch the Qt object so PySide raises if the C++ widget was deleted.
                outliner.objectName()
                return True
            except RuntimeError:
                return False
            except Exception:
                return False

        def _refresh(scene_override=None):
            if not _outliner_alive():
                return
            prev_owner = getattr(card, "_scene_selected_owner", None)
            if not prev_owner:
                try:
                    cur_item = outliner.currentItem()
                    if cur_item is not None:
                        prev_owner = str(cur_item.data(QtCore.Qt.UserRole) or "").strip() or None
                except Exception:
                    prev_owner = None
            try:
                prev_kind = str(getattr(card, "_scene_selected_kind", "") or "").strip().lower()
                if not prev_kind:
                    cur_item = outliner.currentItem()
                    if cur_item is not None:
                        prev_kind = str(cur_item.data(QtCore.Qt.UserRole + 1) or "").strip().lower()
            except Exception:
                prev_kind = ""
            try:
                outliner.blockSignals(True)
            except Exception:
                pass
            outliner.clear()
            scene = scene_override if scene_override is not None else getattr(card, "_graph_scene", None)
            if scene is None:
                empty = QtWidgets.QListWidgetItem("(scene not attached)")
                empty.setFlags(QtCore.Qt.NoItemFlags)
                outliner.addItem(empty)
                try:
                    outliner.blockSignals(False)
                except Exception:
                    pass
                return

            try:
                item = getattr(scene, "_node_items", {}).get(node.name)
            except Exception:
                item = None
            if item is None:
                empty = QtWidgets.QListWidgetItem("(scene node not found)")
                empty.setFlags(QtCore.Qt.NoItemFlags)
                outliner.addItem(empty)
                try:
                    outliner.blockSignals(False)
                except Exception:
                    pass
                return

            rows = []
            seen = set()
            loading_workflow = False
            try:
                win = card.window()
                loading_workflow = bool(getattr(win, "_workflow_load_in_progress", False)) if win is not None else False
            except Exception:
                loading_workflow = False
            try:
                if loading_workflow:
                    asset_rows = list(_quick_outliner_asset_rows(scene, item) or [])
                else:
                    asset_rows = list(_collect_assets(item) or [])
            except Exception:
                asset_rows = []
            for asset in asset_rows:
                if not isinstance(asset, dict):
                    continue
                kind = str(asset.get("kind") or "").strip().lower()
                if kind == "fx_trail":
                    continue
                name = str(asset.get("node") or "").strip()
                if not name or name in seen:
                    continue
                if kind == "camera":
                    seen.add(name)
                    rows.append({"name": name, "path": "", "kind": "camera"})
                    continue
                if kind == "light":
                    seen.add(name)
                    rows.append({"name": name, "path": "", "kind": "light"})
                    continue
                path = str(asset.get("path") or "").strip()
                if not path and not bool(asset.get("_quick")):
                    continue
                ext = Path(path).suffix.lower()
                if not ext:
                    ext = str(asset.get("ext") or "").strip().lower()
                if ext and ext not in SUPPORTED_EXTS:
                    continue
                seen.add(name)
                is_anim_retarget_asset = _is_anim_retarget_kind(kind)
                has_skeleton = _asset_has_scene_skeleton(asset, kind)
                row_kind = "anim_retarget" if is_anim_retarget_asset else "mesh"
                rows.append({"name": name, "path": path, "kind": row_kind, "has_skeleton": bool(has_skeleton)})
                if bool(has_skeleton):
                    rows.append(
                        {
                            "name": name,
                            "path": path,
                            "kind": "skeleton",
                            "display": "Skeleton",
                            "indent": 1,
                            "child": True,
                            "parent": name,
                        }
                    )

            if not rows:
                empty = QtWidgets.QListWidgetItem("(no connected imports, primitives, volumes, UV unwraps, textures, texture layers, texture pros, cameras, or lights)")
                empty.setFlags(QtCore.Qt.NoItemFlags)
                outliner.addItem(empty)
                try:
                    outliner.blockSignals(False)
                except Exception:
                    pass
                return

            hidden = _hidden_set()
            row_widgets = []
            visible_row_number = 0
            for idx, entry in enumerate(rows, start=1):
                name = entry["name"]
                owner_name = str(entry.get("owner") or entry.get("parent") or name).strip()
                display_name = str(entry.get("display") or name).strip()
                row_kind = str(entry.get("kind") or "mesh").strip().lower()
                is_child = bool(entry.get("child")) or row_kind == "skeleton"
                if not is_child:
                    visible_row_number += 1
                display_number = visible_row_number if not is_child else 0
                visible = owner_name not in hidden

                row_widget = QtWidgets.QWidget()
                row_widget.setObjectName("SceneOutlinerRow")
                row_widget.setStyleSheet(
                    "QWidget#SceneOutlinerRow{background:rgba(15,23,42,0.35);}"
                    "QWidget#SceneOutlinerRow[odd=\"true\"]{background:rgba(15,23,42,0.55);}"
                    "QWidget#SceneOutlinerRow[selected=\"true\"]{background:#334155;border-radius:4px;}"
                )
                row_widget.setProperty("selected", False)
                row_widget.setProperty("odd", bool(idx % 2))
                row_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                row_layout = QtWidgets.QHBoxLayout(row_widget)
                row_layout.setContentsMargins(4, 0, 4, 0)
                row_layout.setSpacing(6)

                eye_btn = QtWidgets.QToolButton()
                eye_btn.setAutoRaise(True)
                eye_btn.setCheckable(True)
                eye_btn.setEnabled(not is_child)
                # Avoid firing toggled during list rebuild
                try:
                    eye_btn.blockSignals(True)
                    eye_btn.setChecked(visible)
                finally:
                    eye_btn.blockSignals(False)
                eye_btn.setIcon(_eye_icon(visible) if not is_child else QtGui.QIcon())
                eye_btn.setToolTip("Toggle visibility" if not is_child else "")
                if is_child:
                    try:
                        eye_btn.setVisible(False)
                        eye_btn.setFixedWidth(0)
                    except Exception:
                        pass
                def _on_eye_clicked(checked, n=owner_name, b=eye_btn):
                    # Update the icon immediately; defer visibility side-effects to avoid re-entrancy.
                    try:
                        b.setIcon(_eye_icon(checked))
                    except Exception:
                        pass
                    prev_owner = getattr(card, "_scene_selected_owner", None)
                    QtCore.QTimer.singleShot(0, lambda: _apply_visibility(n, checked))
                    # Keep current selection/gizmo stable when clicking the eye
                    if prev_owner:
                        def _restore_selection(owner=prev_owner):
                            try:
                                for i in range(outliner.count()):
                                    it = outliner.item(i)
                                    if it is None:
                                        continue
                                    if (it.data(QtCore.Qt.UserRole) or "") == owner:
                                        outliner.setCurrentRow(i)
                                        return
                            except Exception:
                                pass
                        QtCore.QTimer.singleShot(0, _restore_selection)
                # Use clicked so programmatic setChecked() during refresh doesn't fire visibility changes.
                eye_btn.clicked.connect(_on_eye_clicked)
                row_layout.addWidget(eye_btn, 0)

                idx_label = QtWidgets.QLabel("  " if is_child else f"{display_number}")
                idx_label.setStyleSheet("color:#94a3b8;background:transparent;")
                row_layout.addWidget(idx_label, 0)

                if is_child:
                    indent_spacer = QtWidgets.QLabel("")
                    indent_spacer.setFixedWidth(18)
                    indent_spacer.setStyleSheet("background:transparent;")
                    row_layout.addWidget(indent_spacer, 0)

                name_edit = QtWidgets.QLineEdit(display_name)
                name_edit.setReadOnly(True)
                name_edit.setFrame(False)
                name_edit.setMinimumWidth(0)
                name_edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                name_edit.setStyleSheet(
                    "QLineEdit{background:transparent;color:#94a3b8;}" if is_child
                    else "QLineEdit{background:transparent;color:#e2e8f0;}"
                )
                name_edit.setFocusPolicy(QtCore.Qt.NoFocus)
                name_edit.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
                name_edit.setProperty("scene_node_name", owner_name)

                def _start_edit(edit=name_edit):
                    if bool(getattr(edit, "_scene_no_rename", False)):
                        return
                    try:
                        prev = getattr(card, "_scene_outliner_editing", None)
                        if isinstance(prev, QtWidgets.QLineEdit) and prev is not edit and not prev.isReadOnly():
                            try:
                                fn = getattr(prev, "_scene_finish_edit", None)
                                if callable(fn):
                                    fn()
                                else:
                                    prev.clearFocus()
                            except Exception:
                                prev.clearFocus()
                    except Exception:
                        pass
                    try:
                        card._scene_outliner_editing = edit
                    except Exception:
                        pass
                    edit.setReadOnly(False)
                    edit.setFrame(True)
                    edit.setStyleSheet(
                        "QLineEdit{background:#12151a;color:#e2e8f0;border:1px solid #3c4450;"
                        "border-radius:4px;padding:2px 6px;}"
                    )
                    edit.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
                    edit.setFocusPolicy(QtCore.Qt.StrongFocus)
                    edit.selectAll()
                    edit.setFocus(QtCore.Qt.MouseFocusReason)

                name_edit._scene_start_edit = lambda e=name_edit: _start_edit(e)

                def _finish_edit(edit=name_edit, scn=scene):
                    if bool(getattr(edit, "_scene_no_rename", False)):
                        return
                    old_name = edit.property("scene_node_name") or ""
                    new_name = edit.text().strip()
                    edit.setReadOnly(True)
                    edit.setFrame(False)
                    edit.setStyleSheet("QLineEdit{background:transparent;color:#e2e8f0;}")
                    edit.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
                    edit.setFocusPolicy(QtCore.Qt.NoFocus)
                    try:
                        if getattr(card, "_scene_outliner_editing", None) is edit:
                            card._scene_outliner_editing = None
                    except Exception:
                        pass
                    if not new_name or new_name == old_name:
                        edit.setText(old_name)
                        return
                    if _apply_rename(scn, old_name, new_name):
                        edit.setProperty("scene_node_name", new_name)
                        _refresh(scn)
                    else:
                        edit.setText(old_name)

                name_edit.editingFinished.connect(_finish_edit)
                name_edit._scene_finish_edit = lambda e=name_edit: _finish_edit(e)
                if is_child:
                    name_edit._scene_no_rename = True
                row_layout.addWidget(name_edit, 1)

                row_item = QtWidgets.QListWidgetItem()
                row_item.setData(QtCore.Qt.UserRole, owner_name)
                row_item.setData(QtCore.Qt.UserRole + 1, (entry.get("kind") or "mesh"))
                if is_child:
                    row_item.setData(QtCore.Qt.UserRole + 2, name)
                if entry.get("path"):
                    row_item.setToolTip(entry["path"])
                if is_child:
                    row_item.setToolTip(f"Show skeleton for {owner_name}")
                try:
                    vw = int(outliner.viewport().width())
                except Exception:
                    vw = 0
                if vw > 0:
                    try:
                        row_widget.setMinimumWidth(vw)
                    except Exception:
                        pass
                try:
                    hint = row_widget.sizeHint()
                    if vw > 0:
                        hint.setWidth(max(vw, hint.width()))
                    row_item.setSizeHint(hint)
                except Exception:
                    row_item.setSizeHint(row_widget.sizeHint())
                outliner.addItem(row_item)
                outliner.setItemWidget(row_item, row_widget)
                row_widgets.append((row_item, row_widget))
                class _RowSelectFilter(QtCore.QObject):
                    def __init__(self, item, label, row_idx, row_name, edit):
                        super().__init__(outliner)
                        self._item = item
                        self._label = label
                        self._idx = row_idx
                        self._name = row_name
                        self._edit = edit
                    def eventFilter(self, obj, event):
                        et = event.type()
                        if et == QtCore.QEvent.MouseButtonPress:
                            try:
                                outliner.setCurrentItem(self._item)
                            except Exception:
                                pass
                            try:
                                _outliner_log(
                                    f"click label={self._label} idx={self._idx} name={self._name} "
                                    f"pos=({event.pos()})"
                                )
                            except Exception:
                                pass
                        if et == QtCore.QEvent.MouseButtonDblClick:
                            try:
                                outliner.setCurrentItem(self._item)
                            except Exception:
                                pass
                            try:
                                self._edit._scene_start_edit()
                            except Exception:
                                pass
                        return False

                for widget, label in (
                    (row_widget, "row"),
                    (idx_label, "idx"),
                    (eye_btn, "eye"),
                    *(([(indent_spacer, "indent")] if is_child else [])),
                ):
                    try:
                        filt = _RowSelectFilter(row_item, label, display_number or idx, display_name, name_edit)
                        widget.installEventFilter(filt)
                        setattr(widget, "_row_select_filter", filt)
                    except Exception:
                        pass

            card._scene_outliner_rows = row_widgets

            # Restore prior selection if possible; otherwise clear selection/gizmo.
            selected_row = None
            if prev_owner:
                try:
                    prev_owner_l = str(prev_owner).strip().lower()
                    prev_kind_l = str(prev_kind or "").strip().lower()
                    for i in range(outliner.count()):
                        it = outliner.item(i)
                        if it is None:
                            continue
                        item_owner = str(it.data(QtCore.Qt.UserRole) or "").strip().lower()
                        item_kind = str(it.data(QtCore.Qt.UserRole + 1) or "").strip().lower()
                        if item_owner == prev_owner_l and (not prev_kind_l or item_kind == prev_kind_l):
                            selected_row = i
                            break
                except Exception:
                    selected_row = None

            try:
                if selected_row is not None:
                    outliner.setCurrentRow(selected_row)
                    try:
                        _update_row_highlight(outliner.currentItem())
                    except Exception:
                        pass
                else:
                    outliner.setCurrentRow(-1)
                    outliner.clearSelection()
                    card._scene_selected_owner = None
                    card._scene_selected_kind = None
                    card._scene_outliner_user_selected = False
                    try:
                        win = card.window()
                        ctl = getattr(win, "_timeline_controller", None) if win is not None else None
                        if ctl is not None:
                            ctl.sync_timeline_context()
                    except Exception:
                        pass
                    try:
                        _update_row_highlight(None)
                    except Exception:
                        pass
                    try:
                        xform_panel.setEnabled(False)
                    except Exception:
                        pass
                    # Hide gizmo when nothing is selected
                    try:
                        glv = _get_glv()
                        if glv is not None:
                            glv._xform_gizmo_owner = None
                            glv._xform_gizmo_owner_kind = None
                            try:
                                if hasattr(glv, "set_scene_asset_uv_overlay"):
                                    glv.set_scene_asset_uv_overlay(None)
                            except Exception:
                                pass
                            glv._xform_gizmo_pos_locked = False
                            # When nothing is selected, park the gizmo at world origin.
                            glv._xform_gizmo_pos = (0.0, 0.0, 0.0)
                            glv.update()
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                outliner.blockSignals(False)
            except Exception:
                pass

            # If we restored a selection, sync panels/gizmo now.
            if selected_row is not None:
                _on_outliner_select()

        def _safe_refresh(scene_override=None):
            if not _outliner_alive():
                return
            try:
                _refresh(scene_override)
            except RuntimeError as exc:
                if "already deleted" in str(exc).lower():
                    return
                raise

        def _connect(scene):
            if scene is None:
                return
            if getattr(card, "_scene_outliner_connected", False):
                return
            try:
                if hasattr(scene, "linksChanged"):
                    scene.linksChanged.connect(lambda *_, scn=scene: _safe_refresh(scn))
                if hasattr(scene, "paramChanged"):
                    scene.paramChanged.connect(lambda *_, scn=scene: _safe_refresh(scn))
                card._scene_outliner_connected = True
            except Exception:
                pass

        card._scene_outliner_refresh = _safe_refresh
        card._scene_outliner_connect = _connect

        # initial populate
        sc = getattr(card, "_graph_scene", None)
        if sc is not None:
            _connect(sc)
            _safe_refresh(sc)
        else:
            _safe_refresh()

        return True

    except Exception:
        print("[SceneSpec] augment_infocard_footer ERROR", flush=True)
        traceback.print_exc()
        return True


def render_node_body(node_item, y_cursor: int) -> int:
    body = SceneAssemblyWidget(node_item, node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = body.sizeHint().height()
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    return y_cursor + h


SCENE_SPEC = Spec(
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
    augment_infocard_footer=augment_infocard_footer,
)
