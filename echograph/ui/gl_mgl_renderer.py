from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from numpy.typing import NDArray as NDArray
else:
    NDArray: TypeAlias = Any

try:
    import numpy as np
except Exception:
    np = None

try:
    import moderngl
except Exception:
    moderngl = None

try:
    from pyrr import Matrix44
except Exception:
    Matrix44 = None

try:
    import openmesh  # type: ignore[reportMissingImports]
except Exception:
    openmesh = None

try:
    from PIL import Image as PILImage
except Exception:
    PILImage = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    _HAS_QT6 = True
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
    _HAS_QT6 = False

from .gl_arcball import _ArcBallUtil
from .gl_debug_geo import debug_camera_wire_vertices, debug_cube_wire_vertices
from .gl_loaders import load_fbx_mesh_arrays_pyassimp
from .gl_loaders import load_fbx_edge_vertices
from .gl_loaders import load_gltf_mesh_arrays
from .gl_loaders import load_model
from .gl_loaders import load_obj_mesh_arrays
from .gl_scene import MGLSceneItem
from .gl_shaders import SHADERS
from .gl_types import MeshArrays, SubMeshData
from echograph.material_debug import material_debug_log as _material_debug_log
from echograph.rigging.fbx_stage3_ingest import ingest_fbx_bind_data
from echograph.rigging.fbx_stage4_animation import ingest_fbx_animation_data
from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.rigging.fbx_stage6_debug import evaluate_skeleton_line_points
from echograph.rigging.fbx_stage7_timeline import (
    clip_marker_frames,
    clip_sample_time_from_timeline_seconds,
)

# OpenGL constants (avoid optional PyOpenGL dependency).
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_SCISSOR_TEST = 0x0C11

_HAS_MGL = moderngl is not None and np is not None and Matrix44 is not None

_SCENE_SKELETON_GREEN = (0.0, 1.0, 0.0, 0.95)
_SCENE_SKELETON_SELECTED_YELLOW = (1.0, 0.86, 0.0, 1.0)
_SCENE_SKELETON_HANDLE_RADIUS_SCALE = 1.92
_SCENE_SKELETON_LINE_WIDTH_SCALE = 0.50
_SCENE_SKELETON_SCREEN_HANDLE_RADIUS_SCALE = 3.0


_THUMB_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec3 v_norm;
out vec3 v_vert;
out vec2 v_uv;
out vec3 v_world_norm;
out vec3 v_world_pos;
out vec4 v_color;
out vec4 v_shadow_pos;
void main() {
    gl_Position = vec4(in_pos.xy, 0.0, 1.0);
    v_uv = in_uv;
    v_norm = vec3(0.0, 0.0, 1.0);
    v_vert = vec3(0.0, 0.0, 0.0);
    v_world_norm = vec3(0.0, 0.0, 1.0);
    v_world_pos = vec3(0.0, 0.0, 0.0);
    v_color = vec4(1.0);
    v_shadow_pos = vec4(0.0, 0.0, 0.0, 1.0);
}
"""


def _safe_clip_signature(clip) -> tuple:
    if clip is None:
        return ("none",)
    try:
        name = str(getattr(clip, "name", "") or "")
    except Exception:
        name = ""
    try:
        start = float(getattr(clip, "start_time", 0.0) or 0.0)
    except Exception:
        start = 0.0
    try:
        end = float(getattr(clip, "end_time", start) or start)
    except Exception:
        end = start
    try:
        track_count = int(len(getattr(clip, "tracks", []) or []))
    except Exception:
        track_count = 0
    return (name, round(start, 6), round(end, 6), track_count)


def _mgl_grid(size: float, steps: int) -> NDArray:
    if np is None:
        raise RuntimeError("numpy unavailable")

    steps = int(max(3, steps))
    if (steps % 2) == 0:
        steps += 1

    u = np.repeat(np.linspace(-size, size, steps), 2)
    v = np.tile([-size, size], steps)
    w = np.zeros(steps * 2)
    lower_grid = 0.0
    y = np.full_like(u, lower_grid)
    # Build grid in XZ plane (Y = constant) so it's visible from the default camera.
    grid = np.concatenate([np.dstack([u, y, v]), np.dstack([v, y, u])])
    return grid


class MGLRendererMixin:
    def _mgl_timeline_fx_enabled(self) -> bool:
        try:
            return bool(getattr(self, "_timeline_fx_instances_enabled", True))
        except Exception:
            return True

    def _mgl_log(self, msg: str) -> None:
        try:
            if not bool(getattr(self, "_mgl_splat_log", True)):
                return
            root = Path(__file__).resolve().parents[2]
            log_dir = root / "logs"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                return
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with (log_dir / "splat_debug.log").open("a", encoding="utf-8") as f:
                f.write(f"{ts} {msg}\n")
        except Exception:
            pass

    def _mgl_log_throttled(self, key: str, msg: str, interval: float = 0.75) -> None:
        try:
            last = float(getattr(self, key, 0.0) or 0.0)
            now = float(time.time())
            if (now - last) < float(interval):
                return
            setattr(self, key, now)
        except Exception:
            pass
        self._mgl_log(msg)

    def _mgl_fbx_joints_log(self, msg: str) -> None:
        if not bool(getattr(self, "_mgl_fbx_joints_log_enabled", False)):
            return
        try:
            root = Path(__file__).resolve().parents[2]
            log_dir = root / "logs"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                return
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with (log_dir / "fbx_joints_debug.log").open("a", encoding="utf-8") as f:
                f.write(f"{ts} {msg}\n")
        except Exception:
            pass

    def _mgl_fbx_joints_log_throttled(self, key: str, msg: str, interval: float = 0.5) -> None:
        try:
            store = getattr(self, "_mgl_fbx_joints_log_times", None)
            if not isinstance(store, dict):
                store = {}
                setattr(self, "_mgl_fbx_joints_log_times", store)
            now = float(time.time())
            last = float(store.get(key, 0.0) or 0.0)
            if (now - last) < float(interval):
                return
            store[key] = now
        except Exception:
            pass
        self._mgl_fbx_joints_log(msg)

    def _mgl_scene_skeleton_log_enabled(self) -> bool:
        try:
            if bool(os.environ.get("ECHOGRAPH_SCENE_DEBUG_VERBOSE")):
                return True
        except Exception:
            pass
        try:
            win = self.window()
        except Exception:
            win = None
        try:
            scene_node = getattr(win, "_active_scene_node", None) if win is not None else None
            for p in (getattr(scene_node, "params", None) or []):
                if (p.get("name") or "").strip().lower() != "debug_log":
                    continue
                return str(p.get("value") or "").strip().lower() in {"1", "true", "yes", "on"}
        except Exception:
            pass
        try:
            for entry in list(getattr(self, "_timeline_scene_assets", None) or []):
                if isinstance(entry, dict) and bool(entry.get("debug_log", False)):
                    return True
        except Exception:
            pass
        return False

    def _mgl_scene_skeleton_log(self, event: str, **fields) -> None:
        if not self._mgl_scene_skeleton_log_enabled():
            return
        try:
            root = Path(__file__).resolve().parents[2]
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            payload = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": str(event)}
            payload.update(fields)
            with (log_dir / "scene_skeleton_debug.log").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        except Exception:
            pass

    def _mgl_scene_skeleton_log_throttled(self, key: str, event: str, interval: float = 0.5, **fields) -> None:
        try:
            store = getattr(self, "_mgl_scene_skeleton_log_times", None)
            if not isinstance(store, dict):
                store = {}
                self._mgl_scene_skeleton_log_times = store
            now = float(time.time())
            last = float(store.get(str(key), 0.0) or 0.0)
            if (now - last) < float(interval):
                return
            store[str(key)] = now
        except Exception:
            pass
        self._mgl_scene_skeleton_log(event, **fields)

    def _mgl_scene_skeleton_context_log_fields(self, context: dict | None) -> dict:
        if not isinstance(context, dict):
            return {"has_context": False, "has_skeleton": False, "joint_count": 0, "has_clip": False, "track_count": 0}
        skeleton = context.get("skeleton")
        clip = context.get("clip")
        try:
            joint_count = int(len(list(getattr(skeleton, "joints", []) or []))) if skeleton is not None else 0
        except Exception:
            joint_count = 0
        try:
            track_count = int(len(list(getattr(clip, "tracks", []) or []))) if clip is not None else 0
        except Exception:
            track_count = 0
        return {
            "has_context": True,
            "has_skeleton": bool(skeleton is not None),
            "joint_count": joint_count,
            "has_clip": bool(clip is not None),
            "track_count": track_count,
        }

    def _mgl_retarget_log(self, event: str, **fields) -> None:
        try:
            root = Path(__file__).resolve().parents[2]
            log_dir = root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            payload = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": str(event)}
            payload.update(fields)
            with (log_dir / "anim_retarget_debug.log").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        except Exception:
            pass

    def _mgl_retarget_points_summary(self, points, *, limit: int = 5) -> dict:
        if np is None:
            return {"count": 0, "bounds_min": None, "bounds_max": None, "diag": 0.0, "first": []}
        try:
            arr = np.asarray(points, dtype="f4").reshape(-1, 3)
        except Exception:
            arr = np.zeros((0, 3), dtype="f4")
        if arr.size == 0:
            return {"count": 0, "bounds_min": None, "bounds_max": None, "diag": 0.0, "first": []}
        try:
            mins = arr.min(axis=0)
            maxs = arr.max(axis=0)
            diag = float(np.linalg.norm(maxs - mins))
        except Exception:
            mins = maxs = np.zeros((3,), dtype="f4")
            diag = 0.0
        return {
            "count": int(arr.shape[0]),
            "bounds_min": [round(float(v), 6) for v in mins[:3]],
            "bounds_max": [round(float(v), 6) for v in maxs[:3]],
            "diag": round(float(diag), 6),
            "first": [[round(float(v), 6) for v in row[:3]] for row in arr[: max(0, int(limit))]],
        }

    def _mgl_fx_log(self, msg: str) -> None:
        if not bool(getattr(self, "_mgl_fx_log_enabled", True)):
            return
        try:
            root = Path(__file__).resolve().parents[2]
            log_dir = root / "logs"
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                return
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with (log_dir / "fx_trail_debug.log").open("a", encoding="utf-8") as f:
                f.write(f"{ts} {msg}\n")
        except Exception:
            pass

    def _mgl_fx_log_throttled(self, key: str, msg: str, interval: float = 0.75) -> None:
        try:
            store = getattr(self, "_mgl_fx_log_times", None)
            if not isinstance(store, dict):
                store = {}
                setattr(self, "_mgl_fx_log_times", store)
            now = float(time.time())
            last = float(store.get(key, 0.0) or 0.0)
            if (now - last) < float(interval):
                return
            store[key] = now
        except Exception:
            pass
        self._mgl_fx_log(msg)

    def _mgl_material_log(self, event: str, **fields) -> None:
        if not bool(getattr(self, "_mgl_material_log_enabled", False)):
            return
        try:
            owners = getattr(self, "_mgl_material_debug_owners", None)
            owner = str(fields.get("owner") or "").strip()
            if owner and isinstance(owners, set) and owners and owner not in owners:
                return
        except Exception:
            pass
        _material_debug_log(event, **fields)

    def _mgl_material_log_throttled(self, key: str, event: str, interval: float = 1.0, **fields) -> None:
        try:
            store = getattr(self, "_mgl_material_log_times", None)
            if not isinstance(store, dict):
                store = {}
                setattr(self, "_mgl_material_log_times", store)
            now = float(time.time())
            last = float(store.get(key, 0.0) or 0.0)
            if (now - last) < float(interval):
                return
            store[key] = now
        except Exception:
            pass
        self._mgl_material_log(event, **fields)

    def _mgl_add_wire_item_from_points(
        self,
        name: str,
        line_points: NDArray,
        visible: bool,
        tag: str,
        owner: Optional[str] = None,
        path_key: Optional[str] = None,
    ) -> Optional[MGLSceneItem]:
        if self._mgl_ctx is None or self._mgl_wire_prog is None or np is None:
            if tag == "scene-rig-joints":
                self._mgl_fbx_joints_log(
                    "wire_from_points skip reason=gl_not_ready "
                    + f"name={name} visible={bool(visible)}"
                )
            return None
        if line_points is None or line_points.size == 0:
            if tag == "scene-rig-joints":
                self._mgl_fbx_joints_log(
                    "wire_from_points skip reason=empty_points "
                    + f"name={name} visible={bool(visible)}"
                )
            return None
        edge_count = int(line_points.shape[0] // 2)
        if edge_count <= 0:
            if tag == "scene-rig-joints":
                self._mgl_fbx_joints_log(
                    "wire_from_points skip reason=edge_count_zero "
                    + f"name={name} points={int(line_points.shape[0])}"
                )
            return None
        verts: List[float] = []
        for i in range(edge_count):
            p0 = line_points[i * 2]
            p1 = line_points[i * 2 + 1]
            ax, ay, az = float(p0[0]), float(p0[1]), float(p0[2])
            bx, by, bz = float(p1[0]), float(p1[1]), float(p1[2])
            if ax == bx and ay == by and az == bz:
                continue
            # triangle 1
            verts.extend([ax, ay, az, ax, ay, az, bx, by, bz, -1.0])
            verts.extend([ax, ay, az, ax, ay, az, bx, by, bz, 1.0])
            verts.extend([bx, by, bz, ax, ay, az, bx, by, bz, 1.0])
            # triangle 2
            verts.extend([ax, ay, az, ax, ay, az, bx, by, bz, -1.0])
            verts.extend([bx, by, bz, ax, ay, az, bx, by, bz, 1.0])
            verts.extend([bx, by, bz, ax, ay, az, bx, by, bz, -1.0])
        if not verts:
            if tag == "scene-rig-joints":
                self._mgl_fbx_joints_log(
                    "wire_from_points skip reason=all_degenerate "
                    + f"name={name} edges={edge_count}"
                )
            return None
        try:
            vbo = self._mgl_ctx.buffer(np.array(verts, dtype="f4").tobytes())
            vao_content = [(vbo, "3f 3f 3f 1f", "in_pos", "in_start", "in_end", "in_side")]
            vao = self._mgl_ctx.vertex_array(self._mgl_wire_prog, vao_content)
        except Exception:
            if tag == "scene-rig-joints":
                self._mgl_fbx_joints_log(
                    "wire_from_points skip reason=vao_create_failed "
                    + f"name={name} edges={edge_count}"
                )
            return None
        payload = {
            "vao": vao,
            "color": self._mgl_wire_color,
            "mode": moderngl.TRIANGLES,
        }
        if owner:
            payload["owner"] = owner
        if path_key:
            payload["path"] = path_key
        item = MGLSceneItem(
            name=name,
            draw_fn=MGLRendererMixin._mgl_draw_scene_wire,
            payload=payload,
            resources=[vao, vbo],
            visible=visible,
            order=15,
            tag=tag,
        )
        if tag == "scene-rig-joints":
            self._mgl_fbx_joints_log_throttled(
                f"_mgl_fbx_wire_item_{name}",
                "wire_from_points ok "
                + f"name={name} edges={edge_count} visible={bool(visible)} owner={owner or ''} path={path_key or ''}",
                interval=0.35,
            )
        return item

    def _mgl_timeline_frame_index(self) -> int:
        frame_fn = getattr(self, "_timeline_current_frame", None)
        if not callable(frame_fn):
            return 0
        try:
            return max(0, int(frame_fn()))
        except Exception:
            return 0

    def _mgl_timeline_fps_value(self) -> float:
        try:
            override = float(getattr(self, "_mgl_render_fps_override", 0.0) or 0.0)
        except Exception:
            override = 0.0
        if override > 1.0e-6:
            return float(override)
        try:
            fps = float(getattr(self, "_timeline_fps", 24.0) or 24.0)
        except Exception:
            fps = 24.0
        if fps <= 1.0e-6:
            fps = 24.0
        return float(fps)

    def _mgl_timeline_time_seconds(self) -> float:
        return float(self._mgl_timeline_frame_index()) / float(self._mgl_timeline_fps_value())

    def _mgl_fbx_context_timeline_sample_seconds(self, context: dict | None, owner: str | None = None) -> float:
        timeline_seconds = self._mgl_timeline_time_seconds()
        owner_key = str(owner or "").strip()
        mapped_by_composition = False
        if owner_key:
            try:
                map_fn = getattr(self, "_timeline_composition_sample_seconds_for_owner", None)
                if callable(map_fn):
                    mapped = map_fn(
                        owner_key,
                        self._mgl_timeline_frame_index(),
                        self._mgl_timeline_fps_value(),
                    )
                    if mapped is not None:
                        timeline_seconds = max(0.0, float(mapped))
                        mapped_by_composition = True
            except Exception:
                mapped_by_composition = False
        if owner_key and not bool(mapped_by_composition):
            try:
                speed_fn = getattr(self, "_timeline_owner_speed_factor", None)
                if callable(speed_fn):
                    timeline_seconds *= float(speed_fn(owner_key))
                else:
                    speeds = getattr(self, "_timeline_owner_speed_percent_by_owner", None)
                    pct = None
                    if isinstance(speeds, dict):
                        pct = speeds.get(owner_key, None)
                        if pct is None:
                            owner_l = owner_key.lower()
                            for maybe_key, maybe_val in speeds.items():
                                try:
                                    if str(maybe_key).strip().lower() == owner_l:
                                        pct = maybe_val
                                        break
                                except Exception:
                                    continue
                    if pct is not None:
                        timeline_seconds *= max(0.01, float(pct) / 100.0)
            except Exception:
                pass
        if not isinstance(context, dict):
            return timeline_seconds
        clip = context.get("clip")
        return clip_sample_time_from_timeline_seconds(clip, timeline_seconds)

    def _mgl_fbx_rig_context_for_path(self, path: Path) -> Optional[dict]:
        try:
            path_obj = Path(path)
        except Exception:
            return None
        try:
            cache_key = str(path_obj.resolve())
        except Exception:
            cache_key = str(path_obj)
        try:
            mtime = float(path_obj.stat().st_mtime)
        except Exception:
            mtime = None

        cache = getattr(self, "_mgl_fbx_rig_context_cache", None)
        if not isinstance(cache, dict):
            cache = {}
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("mtime", None) == mtime:
            context = cached.get("context", None)
            self._mgl_fbx_joints_log_throttled(
                f"_mgl_fbx_context_cache_{cache_key}",
                "context cache "
                + f"path={path_obj} hit=True has_context={bool(isinstance(context, dict))} "
                + f"has_skeleton={bool(isinstance(context, dict) and context.get('skeleton') is not None)}",
                interval=1.0,
            )
            return context if isinstance(context, dict) else None

        context = None
        try:
            if path_obj.suffix.lower() == ".bvh":
                animation_result = ingest_bvh_animation_data(path_obj)
                skeleton = getattr(animation_result, "skeleton", None)
                clips = list(getattr(animation_result, "clips", []) or [])
                context = {
                    "skeleton": skeleton,
                    "clip": clips[0] if clips else None,
                    "meshes": [],
                    "loop": True,
                    "mesh_skinning_enabled": False,
                    "show_capture_joints": False,
                    "show_animated_joints": True,
                    "source_format": "bvh",
                }
            else:
                bind_result = ingest_fbx_bind_data(path_obj)
                skeleton = getattr(bind_result, "skeleton", None)
                if skeleton is not None:
                    meshes = list(getattr(bind_result, "meshes", []) or [])
                    clip = None
                    try:
                        animation_result = ingest_fbx_animation_data(path_obj, skeleton=skeleton)
                        clips = list(getattr(animation_result, "clips", []) or [])
                        if clips:
                            clip = clips[0]
                    except Exception:
                        clip = None
                    context = {
                        "skeleton": skeleton,
                        "clip": clip,
                        "meshes": meshes,
                        "loop": True,
                        "show_capture_joints": False,
                        "show_animated_joints": False,
                    }
        except Exception as exc:
            context = None
            self._mgl_fbx_joints_log(
                "context ingest failed "
                + f"path={path_obj} err={exc!r}"
            )

        cache[cache_key] = {"mtime": mtime, "context": context}
        self._mgl_fbx_rig_context_cache = cache
        self._mgl_fbx_joints_log(
            "context ingest "
            + f"path={path_obj} has_context={bool(isinstance(context, dict))} "
            + f"has_skeleton={bool(isinstance(context, dict) and context.get('skeleton') is not None)} "
            + f"mesh_count={int(len(list(context.get('meshes') or []))) if isinstance(context, dict) else 0} "
            + f"has_clip={bool(isinstance(context, dict) and context.get('clip') is not None)}"
        )
        return context if isinstance(context, dict) else None

    def _mgl_scene_owner_fbx_rig_context(self, owner: str) -> Optional[dict]:
        scene = getattr(self, "_mgl_scene", None)
        owner_key = str(owner or "").strip()
        if not owner_key:
            return None
        owner_key_norm = owner_key.lower()
        if scene is not None:
            for tag in ("scene-model", "scene-wire", "scene-rig-joints", "model"):
                try:
                    items = list(scene.iter_by_tag(tag))
                except Exception:
                    items = []
                for item in items:
                    payload = getattr(item, "payload", None) or {}
                    payload_owner = str(payload.get("owner") or "").strip()
                    if payload_owner and payload_owner.lower() != owner_key_norm:
                        continue
                    context = payload.get("fbx_rig_context")
                    if isinstance(context, dict):
                        return context
                    path_text = str(payload.get("path") or "").strip()
                    if path_text.lower().endswith((".fbx", ".bvh")):
                        try:
                            context = self._mgl_fbx_rig_context_for_path(Path(path_text))
                        except Exception:
                            context = None
                        if isinstance(context, dict):
                            try:
                                payload["fbx_rig_context"] = context
                                item.payload = payload
                            except Exception:
                                pass
                            return context
        for asset in list(getattr(self, "_timeline_scene_assets", None) or []):
            if not isinstance(asset, dict):
                continue
            asset_owner = str(asset.get("node") or asset.get("owner") or "").strip()
            if asset_owner and asset_owner.lower() != owner_key_norm:
                continue
            context = asset.get("fbx_rig_context")
            if isinstance(context, dict):
                return context
            path_text = str(asset.get("path") or "").strip()
            if path_text.lower().endswith((".fbx", ".bvh")):
                try:
                    context = self._mgl_fbx_rig_context_for_path(Path(path_text))
                except Exception:
                    context = None
                if isinstance(context, dict):
                    return context
        return None

    def _mgl_fbx_clip_marker_keys_map(self, owner: str, context: dict) -> Dict[int, Dict[str, object]]:
        clip = context.get("clip") if isinstance(context, dict) else None
        if clip is None:
            return {}

        owner_key = str(owner or "").strip().lower()
        fps_value = float(self._mgl_timeline_fps_value())
        cache = getattr(self, "_mgl_fbx_clip_keys_cache", None)
        if not isinstance(cache, dict):
            cache = {}

        cache_key = (
            owner_key,
            id(clip),
            round(float(fps_value), 6),
            _safe_clip_signature(clip),
        )
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            keys_map = cached.get("keys")
            if isinstance(keys_map, dict):
                return keys_map

        frames = clip_marker_frames(clip, fps=fps_value)
        keys_map: Dict[int, Dict[str, object]] = {}
        for frame in frames:
            f = int(frame)
            if f < 0:
                continue
            keys_map[f] = {"fbx_clip_key": True}
        cache[cache_key] = {"keys": keys_map}

        if len(cache) > 32:
            # Keep cache bounded; deterministic order is not required here.
            try:
                for stale_key in list(cache.keys())[:-32]:
                    del cache[stale_key]
            except Exception:
                pass

        self._mgl_fbx_clip_keys_cache = cache
        return keys_map

    def _mgl_refresh_fbx_rig_wire_item(self, item: MGLSceneItem) -> None:
        if np is None:
            self._mgl_fbx_joints_log("refresh skip reason=numpy_unavailable")
            return
        payload = item.payload or {}
        context = payload.get("fbx_rig_context")
        if not isinstance(context, dict):
            if str(getattr(item, "tag", "") or "") == "scene-rig-joints":
                self._mgl_fbx_joints_log(
                    "refresh skip reason=no_context "
                    + f"item={item.name} id={getattr(item, 'item_id', 0)}"
                )
            return

        pose_mode = str(payload.get("fbx_rig_pose_mode", "animated") or "animated").strip().lower()
        owner_for_pose = str(payload.get("owner") or "").strip()
        target_pose_edit_static = False
        try:
            target_pose_edit_static = (
                self._mgl_retarget_pick_mode() == "target_pose"
                and owner_for_pose
                and owner_for_pose.lower() == self._mgl_retarget_target_owner().lower()
            )
        except Exception:
            target_pose_edit_static = False
        static_frame = bool(context.get("retarget_static_pose", False)) or target_pose_edit_static
        frame_key = (
            ("capture", 0)
            if pose_mode in {"capture", "bind", "rest", "capture_pose"}
            else (
                "animated",
                0 if static_frame else self._mgl_timeline_frame_index(),
                "static" if static_frame else "clip",
            )
        )
        if payload.get("_fbx_rig_frame", None) == frame_key and payload.get("vao") is not None:
            return

        skeleton = context.get("skeleton")
        if skeleton is None:
            self._mgl_fbx_joints_log(
                "refresh skip reason=no_skeleton "
                + f"item={item.name} id={getattr(item, 'item_id', 0)} mode={pose_mode}"
            )
            return
        if pose_mode in {"capture", "bind", "rest", "capture_pose"}:
            clip = None
            sample_time = 0.0
            loop = True
        else:
            if static_frame:
                clip = None
                sample_time = 0.0
            else:
                clip = context.get("clip")
                sample_time = self._mgl_fbx_context_timeline_sample_seconds(context, payload.get("owner"))
            loop = bool(context.get("loop", True))
        line_points = self._mgl_fbx_joint_line_points(
            skeleton,
            clip,
            sample_time,
            loop=loop,
            prefer_inverse_bind=bool(pose_mode in {"capture", "bind", "rest", "capture_pose"}),
        )
        owner = payload.get("owner")
        if bool(payload.get("scene_skeleton_overlay", False)) and np is not None:
            try:
                line_points, _display_info = self._mgl_scene_skeleton_display_positions(str(owner or ""), line_points)
            except Exception:
                pass
        path_key = payload.get("path")
        color = payload.get("color")
        line_width = payload.get("line_width")

        old_resources = list(item.resources or [])
        old_payload = dict(payload)
        replacement = self._mgl_add_wire_item_from_points(
            name=item.name,
            line_points=line_points,
            visible=item.visible,
            tag=item.tag or "scene-wire",
            owner=str(owner or "") if owner is not None else None,
            path_key=str(path_key or "") if path_key is not None else None,
        )
        if replacement is None:
            old_payload["_fbx_rig_refresh_failed_frame"] = frame_key
            item.payload = old_payload
            self._mgl_fbx_joints_log(
                "refresh replacement_missing "
                + f"item={item.name} id={getattr(item, 'item_id', 0)} mode={pose_mode} "
                + f"segments={int(line_points.shape[0] // 2)} visible={bool(item.visible)} "
                + f"owner={owner or ''} path={path_key or ''}"
            )
            return
        else:
            new_payload = dict(replacement.payload or {})
            new_payload["fbx_rig_context"] = context
            new_payload["_fbx_rig_frame"] = frame_key
            new_payload["fbx_rig_pose_mode"] = pose_mode
            if bool(old_payload.get("scene_skeleton_overlay", False)):
                new_payload["scene_skeleton_overlay"] = True
                new_payload["ignore_owner_model"] = True
            if old_payload.get("model") is not None:
                new_payload["model"] = old_payload.get("model")
            if color is not None:
                new_payload["color"] = color
            if line_width is not None:
                new_payload["line_width"] = line_width
            if "xray" in payload:
                new_payload["xray"] = bool(payload.get("xray"))
            if "xray_back_alpha" in payload:
                try:
                    new_payload["xray_back_alpha"] = float(payload.get("xray_back_alpha", 0.25) or 0.25)
                except Exception:
                    pass
            try:
                new_payload["segment_count"] = int(line_points.shape[0] // 2)
                new_payload["bounds_min"] = line_points.min(axis=0).astype("f4")
                new_payload["bounds_max"] = line_points.max(axis=0).astype("f4")
            except Exception:
                pass
            item.payload = new_payload
            item.resources = list(replacement.resources or [])
            self._mgl_fbx_joints_log_throttled(
                f"_mgl_fbx_refresh_ok_{getattr(item, 'item_id', 0)}",
                "refresh ok "
                + f"item={item.name} id={getattr(item, 'item_id', 0)} mode={pose_mode} "
                + f"segments={int(line_points.shape[0] // 2)} visible={bool(item.visible)} "
                + f"owner={owner or ''} path={path_key or ''}",
                interval=0.4,
            )

        for res in old_resources:
            if res is not None and hasattr(res, "release"):
                try:
                    res.release()
                except Exception:
                    pass

    def _mgl_fbx_bind_positions_array(self, mesh_obj: Any, vertex_count: int) -> Optional[NDArray]:
        if np is None:
            return None
        count = max(0, int(vertex_count))
        if count <= 0:
            return None
        metadata = getattr(mesh_obj, "metadata", None)
        bind_positions = []
        if isinstance(metadata, dict):
            bind_positions = list(metadata.get("bind_positions") or [])
        if not bind_positions:
            return None
        out = np.zeros((count, 3), dtype="f4")
        limit = min(count, len(bind_positions))
        for idx in range(limit):
            try:
                row = bind_positions[idx]
                out[idx, 0] = float(row[0])  # type: ignore[index]
                out[idx, 1] = float(row[1])  # type: ignore[index]
                out[idx, 2] = float(row[2])  # type: ignore[index]
            except Exception:
                continue
        return out

    def _mgl_fbx_triangle_normals(self, tri_points: NDArray) -> NDArray:
        if np is None:
            raise RuntimeError("numpy unavailable")
        pts = np.asarray(tri_points, dtype="f4").reshape(-1, 3)
        tri_count = int(pts.shape[0] // 3)
        if tri_count <= 0:
            return np.zeros((0, 3), dtype="f4")
        tri = pts[: tri_count * 3].reshape(-1, 3, 3)
        v1 = tri[:, 1] - tri[:, 0]
        v2 = tri[:, 2] - tri[:, 0]
        n = np.cross(v1, v2)
        lengths = np.linalg.norm(n, axis=1)
        lengths[lengths < 1.0e-8] = 1.0
        n = (n.T / lengths).T
        return np.repeat(n[:, None, :], 3, axis=1).reshape(-1, 3).astype("f4", copy=False)

    def _mgl_fbx_skin_specs_from_context(self, context: dict) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        if np is None or not isinstance(context, dict):
            return specs
        meshes = list(context.get("meshes") or [])
        for mesh_obj in meshes:
            try:
                vertex_count = int(getattr(mesh_obj, "vertex_count", 0) or 0)
            except Exception:
                vertex_count = 0
            if vertex_count <= 0:
                continue

            bind_positions = self._mgl_fbx_bind_positions_array(mesh_obj, vertex_count)
            if bind_positions is None or bind_positions.size == 0:
                continue

            tri_raw = list(getattr(mesh_obj, "triangle_indices", None) or [])
            if not tri_raw:
                continue
            tri_idx_list: List[int] = []
            for value in tri_raw:
                try:
                    vid = int(value)
                except Exception:
                    continue
                if 0 <= vid < vertex_count:
                    tri_idx_list.append(vid)
            tri_count = int((len(tri_idx_list) // 3) * 3)
            if tri_count <= 0:
                continue
            tri_indices = np.asarray(tri_idx_list[:tri_count], dtype=np.int64)
            tri_points = bind_positions[tri_indices].reshape(-1, 3).astype("f4", copy=False)
            tri_normals = self._mgl_fbx_triangle_normals(tri_points)

            skins = list(getattr(mesh_obj, "vertex_skins", None) or [])
            max_influences = 0
            for skin in skins:
                max_influences = max(
                    max_influences,
                    len(list(getattr(skin, "influences", None) or [])),
                )
            max_influences = max(1, int(max_influences))
            joint_indices = np.full((vertex_count, max_influences), -1, dtype=np.int32)
            joint_weights = np.zeros((vertex_count, max_influences), dtype=np.float32)

            for skin in skins:
                try:
                    vid = int(getattr(skin, "vertex_index", -1))
                except Exception:
                    vid = -1
                if vid < 0 or vid >= vertex_count:
                    continue
                influences = list(getattr(skin, "influences", None) or [])
                for slot, influence in enumerate(influences[:max_influences]):
                    try:
                        joint = int(getattr(influence, "joint_index", -1))
                        weight = float(getattr(influence, "weight", 0.0) or 0.0)
                    except Exception:
                        joint = -1
                        weight = 0.0
                    if joint < 0 or weight <= 0.0:
                        continue
                    joint_indices[vid, slot] = joint
                    joint_weights[vid, slot] = weight

            totals = joint_weights.sum(axis=1, keepdims=True)
            valid = totals[:, 0] > 1.0e-8
            if np.any(valid):
                joint_weights[valid] = joint_weights[valid] / totals[valid]

            specs.append(
                {
                    "name": str(getattr(mesh_obj, "name", "") or ""),
                    "bind_positions": bind_positions,
                    "triangle_indices": tri_indices,
                    "bind_tri_points": tri_points,
                    "bind_tri_normals": tri_normals,
                    "joint_indices": joint_indices,
                    "joint_weights": joint_weights,
                }
            )
        return specs

    def _mgl_fbx_mesh_arrays_from_context(self, context: dict | None) -> Optional[MeshArrays]:
        if np is None or not isinstance(context, dict):
            return None
        specs = self._mgl_fbx_skin_specs_from_context(context)
        if not specs:
            return None
        submeshes: List[SubMeshData] = []
        points_all: List[NDArray] = []
        normals_all: List[NDArray] = []
        uvs_all: List[NDArray] = []
        for spec in specs:
            points = np.asarray(spec.get("bind_tri_points"), dtype="f4").reshape(-1, 3)
            normals = np.asarray(spec.get("bind_tri_normals"), dtype="f4").reshape(-1, 3)
            if points.size == 0:
                continue
            if normals.size != points.size:
                normals = self._mgl_fbx_triangle_normals(points)
            uvs = np.zeros((points.shape[0], 2), dtype="f4")
            submeshes.append(SubMeshData(points=points, normals=normals, uvs=uvs))
            points_all.append(points)
            normals_all.append(normals)
            uvs_all.append(uvs)
        if not points_all:
            return None
        return MeshArrays(
            points=np.concatenate(points_all, axis=0).astype("f4", copy=False),
            normals=np.concatenate(normals_all, axis=0).astype("f4", copy=False),
            uvs=np.concatenate(uvs_all, axis=0).astype("f4", copy=False),
            submeshes=submeshes,
        )

    @staticmethod
    def _mgl_fbx_runtime_influence_slots(
        joint_indices: NDArray,
        joint_weights: NDArray,
        joint_count: int,
    ) -> Tuple[List[Dict[str, NDArray]], NDArray]:
        if np is None:
            return [], np.zeros((0,), dtype=np.int64)
        try:
            ji = np.asarray(joint_indices, dtype=np.int32)
            jw = np.asarray(joint_weights, dtype="f4")
        except Exception:
            return [], np.zeros((0,), dtype=np.int64)
        if ji.ndim != 2 or jw.ndim != 2 or ji.shape != jw.shape:
            return [], np.zeros((0,), dtype=np.int64)
        vertex_count = int(ji.shape[0])
        if vertex_count <= 0:
            return [], np.zeros((0,), dtype=np.int64)
        try:
            slot_count = int(ji.shape[1])
        except Exception:
            slot_count = 0
        if slot_count <= 0:
            return [], np.arange(vertex_count, dtype=np.int64)
        jc = max(0, int(joint_count))
        weighted_mask = np.zeros((vertex_count,), dtype=bool)
        slots: List[Dict[str, NDArray]] = []
        for slot in range(slot_count):
            js = ji[:, slot]
            ws = jw[:, slot]
            valid = (js >= 0) & (js < jc) & (ws > 1.0e-8)
            if not np.any(valid):
                continue
            vids = np.nonzero(valid)[0].astype(np.int64, copy=False)
            slots.append(
                {
                    "vertex_indices": vids,
                    "joint_indices": js[valid].astype(np.int32, copy=False),
                    "weights": ws[valid].astype("f4", copy=False),
                }
            )
            weighted_mask[valid] = True
        unweighted = np.nonzero(~weighted_mask)[0].astype(np.int64, copy=False)
        return slots, unweighted

    @staticmethod
    def _mgl_fbx_mesh_skinning_enabled(context: dict | None) -> bool:
        if not isinstance(context, dict):
            return True
        raw = context.get("mesh_skinning_enabled", True)
        if isinstance(raw, str):
            token = raw.strip().lower()
            if token in {"0", "false", "no", "off"}:
                return False
            if token in {"1", "true", "yes", "on"}:
                return True
            return True
        try:
            return bool(raw)
        except Exception:
            return True

    @staticmethod
    def _mgl_fbx_skin_weight_debug_enabled(context: dict | None) -> bool:
        if not isinstance(context, dict):
            return False
        raw = context.get("skin_weight_debug", context.get("show_skin_weights", False))
        if isinstance(raw, str):
            token = raw.strip().lower()
            if token in {"1", "true", "yes", "on"}:
                return True
            if token in {"0", "false", "no", "off"}:
                return False
            return False
        try:
            return bool(raw)
        except Exception:
            return False

    @staticmethod
    def _mgl_fbx_capture_joints_debug_enabled(context: dict | None) -> bool:
        if not isinstance(context, dict):
            return False
        raw = context.get("show_capture_joints", context.get("capture_joint_debug", False))
        if isinstance(raw, str):
            token = raw.strip().lower()
            if token in {"1", "true", "yes", "on"}:
                return True
            if token in {"0", "false", "no", "off"}:
                return False
            return False
        try:
            return bool(raw)
        except Exception:
            return False

    @staticmethod
    def _mgl_fbx_animated_joints_debug_enabled(context: dict | None) -> bool:
        if not isinstance(context, dict):
            return False
        raw = context.get("show_animated_joints", context.get("animated_joint_debug", False))
        if isinstance(raw, str):
            token = raw.strip().lower()
            if token in {"1", "true", "yes", "on"}:
                return True
            if token in {"0", "false", "no", "off"}:
                return False
            return False
        try:
            return bool(raw)
        except Exception:
            return False

    def _mgl_fbx_joint_debug_modes(self, context: dict | None) -> Tuple[bool, bool]:
        capture_enabled = self._mgl_fbx_capture_joints_debug_enabled(context)
        animated_enabled = self._mgl_fbx_animated_joints_debug_enabled(context)
        if capture_enabled and animated_enabled:
            # Normalize stale contexts from older builds that allowed both toggles at once.
            animated_enabled = False
        return bool(capture_enabled), bool(animated_enabled)

    def _mgl_fbx_bind_joints_only_enabled(self, context: dict | None) -> bool:
        # "Bind joints" debug mode intentionally hides mesh geometry and leaves only joint overlays.
        capture_enabled, _animated_enabled = self._mgl_fbx_joint_debug_modes(context)
        return bool(capture_enabled)

    def _mgl_fbx_joint_line_points(
        self,
        skeleton,
        clip,
        sample_time: float,
        *,
        loop: bool,
        prefer_inverse_bind: bool = False,
    ) -> NDArray:
        if np is None or skeleton is None:
            return np.zeros((0, 3), dtype="f4")

        line_points = np.zeros((0, 3), dtype="f4")
        line_source = "none"
        line_from_stage6 = np.zeros((0, 3), dtype="f4")

        def _segments_all_degenerate(points: NDArray, eps: float = 1.0e-7) -> bool:
            try:
                arr = np.asarray(points, dtype="f4").reshape(-1, 3)
            except Exception:
                return True
            if arr.size == 0:
                return True
            edge_count = int(arr.shape[0] // 2)
            if edge_count <= 0:
                return True
            try:
                seg = arr[: edge_count * 2].reshape(edge_count, 2, 3)
                lens = np.linalg.norm(seg[:, 1, :] - seg[:, 0, :], axis=1)
                return bool(np.count_nonzero(lens > float(eps)) <= 0)
            except Exception:
                return True

        def _segments_all_exact_degenerate(points: NDArray) -> bool:
            try:
                arr = np.asarray(points, dtype="f4").reshape(-1, 3)
            except Exception:
                return True
            if arr.size == 0:
                return True
            edge_count = int(arr.shape[0] // 2)
            if edge_count <= 0:
                return True
            try:
                seg = arr[: edge_count * 2].reshape(edge_count, 2, 3)
                exact_equal = (
                    (seg[:, 0, 0] == seg[:, 1, 0])
                    & (seg[:, 0, 1] == seg[:, 1, 1])
                    & (seg[:, 0, 2] == seg[:, 1, 2])
                )
                return bool(np.count_nonzero(~exact_equal) <= 0)
            except Exception:
                return True

        def _positions_collapsed(points: NDArray, eps: float = 1.0e-7) -> bool:
            try:
                arr = np.asarray(points, dtype="f4").reshape(-1, 3)
            except Exception:
                return True
            if arr.size == 0:
                return True
            try:
                mins = arr.min(axis=0)
                maxs = arr.max(axis=0)
                return float(np.linalg.norm(maxs - mins)) <= float(eps)
            except Exception:
                return True

        def _positions_diag(points: NDArray) -> float:
            try:
                arr = np.asarray(points, dtype="f4").reshape(-1, 3)
            except Exception:
                return 0.0
            if arr.size == 0:
                return 0.0
            try:
                mins = arr.min(axis=0)
                maxs = arr.max(axis=0)
                d = float(np.linalg.norm(maxs - mins))
                if not math.isfinite(d):
                    return 0.0
                return max(0.0, d)
            except Exception:
                return 0.0

        def _inverse_bind_positions() -> Tuple[NDArray, str]:
            joints = list(getattr(skeleton, "joints", []) or [])
            if not joints:
                return np.zeros((0, 3), dtype="f4"), "inverse_bind_none"
            inv_rows_tcol: List[Tuple[float, float, float]] = []
            inv_rows_trow: List[Tuple[float, float, float]] = []
            for joint in joints:
                raw_inv = tuple(getattr(joint, "inverse_bind_matrix", ()) or ())
                if len(raw_inv) != 16:
                    inv_rows_tcol.append((0.0, 0.0, 0.0))
                    inv_rows_trow.append((0.0, 0.0, 0.0))
                    continue
                try:
                    inv_bind = np.asarray(raw_inv, dtype="f4").reshape(4, 4)
                    bind_global = np.linalg.inv(inv_bind)
                    flat = bind_global.reshape(-1)
                    inv_rows_tcol.append((float(flat[3]), float(flat[7]), float(flat[11])))
                    inv_rows_trow.append((float(flat[12]), float(flat[13]), float(flat[14])))
                except Exception:
                    inv_rows_tcol.append((0.0, 0.0, 0.0))
                    inv_rows_trow.append((0.0, 0.0, 0.0))
                    continue
            if not (inv_rows_tcol or inv_rows_trow):
                return np.zeros((0, 3), dtype="f4"), "inverse_bind_empty"
            try:
                inv_tcol = (
                    np.asarray(inv_rows_tcol, dtype="f4").reshape(-1, 3)
                    if inv_rows_tcol
                    else np.zeros((0, 3), dtype="f4")
                )
                inv_trow = (
                    np.asarray(inv_rows_trow, dtype="f4").reshape(-1, 3)
                    if inv_rows_trow
                    else np.zeros((0, 3), dtype="f4")
                )
            except Exception:
                return np.zeros((0, 3), dtype="f4"), "inverse_bind_parse_error"
            diag_inv_tcol = _positions_diag(inv_tcol)
            diag_inv_trow = _positions_diag(inv_trow)
            if diag_inv_trow > diag_inv_tcol:
                return np.asarray(inv_trow, dtype="f4").reshape(-1, 3), "inverse_bind_trow_positions"
            return np.asarray(inv_tcol, dtype="f4").reshape(-1, 3), "inverse_bind_tcol_positions"

        try:
            sample = evaluate_skeleton_line_points(
                skeleton,
                clip,
                float(sample_time),
                loop=bool(loop),
            )
            line_from_stage6 = np.array(sample.line_points or [], dtype="f4").reshape(-1, 3)
            line_points = line_from_stage6
            if (
                line_points.size
                and (not _segments_all_degenerate(line_points))
                and (not _segments_all_exact_degenerate(line_points))
            ):
                line_source = "stage6_eval"
            elif line_points.size:
                line_points = np.zeros((0, 3), dtype="f4")
                line_source = "stage6_eval_degenerate"
        except Exception:
            line_points = np.zeros((0, 3), dtype="f4")

        joint_positions = np.zeros((0, 3), dtype="f4")
        try:
            evaluation = evaluate_rig_at_time(
                skeleton=skeleton,
                clip=clip,
                time_seconds=float(sample_time),
                loop=bool(loop),
            )
            rows_tcol: List[Tuple[float, float, float]] = []
            rows_trow: List[Tuple[float, float, float]] = []
            for matrix in list(getattr(evaluation, "global_matrices", []) or []):
                values = tuple(matrix or ())
                if len(values) != 16:
                    continue
                rows_tcol.append((float(values[3]), float(values[7]), float(values[11])))
                rows_trow.append((float(values[12]), float(values[13]), float(values[14])))
            cand_tcol = (
                np.asarray(rows_tcol, dtype="f4").reshape(-1, 3)
                if rows_tcol
                else np.zeros((0, 3), dtype="f4")
            )
            cand_trow = (
                np.asarray(rows_trow, dtype="f4").reshape(-1, 3)
                if rows_trow
                else np.zeros((0, 3), dtype="f4")
            )
            diag_tcol = _positions_diag(cand_tcol)
            diag_trow = _positions_diag(cand_trow)
            if diag_trow > diag_tcol:
                joint_positions = cand_trow
                if line_source in {"none", "stage6_eval_degenerate"}:
                    line_source = "eval_trow_positions"
            else:
                joint_positions = cand_tcol
                if line_source in {"none", "stage6_eval_degenerate"}:
                    line_source = "eval_tcol_positions"
        except Exception:
            joint_positions = np.zeros((0, 3), dtype="f4")

        if bool(prefer_inverse_bind):
            preferred_positions, preferred_source = _inverse_bind_positions()
            if preferred_positions.size and not _positions_collapsed(preferred_positions):
                joint_positions = preferred_positions
                line_points = np.zeros((0, 3), dtype="f4")
                line_source = f"preferred_{preferred_source}"

        if joint_positions.size == 0 or _positions_collapsed(joint_positions):
            inv_positions, inv_source = _inverse_bind_positions()
            if inv_positions.size and not _positions_collapsed(inv_positions):
                joint_positions = inv_positions
                if line_source in {"none", "stage6_eval_degenerate"}:
                    line_source = inv_source

        if joint_positions.size == 0 or _positions_collapsed(joint_positions):
            joints = list(getattr(skeleton, "joints", []) or [])
            if joints:
                cached_world: Dict[int, NDArray] = {}
                resolving: set[int] = set()

                def _joint_local_translation(index: int) -> NDArray:
                    try:
                        joint = joints[int(index)]
                    except Exception:
                        return np.zeros((3,), dtype="f4")
                    local_bind = getattr(joint, "local_bind", None)
                    raw = getattr(local_bind, "translation", (0.0, 0.0, 0.0))
                    try:
                        return np.array(
                            [float(raw[0]), float(raw[1]), float(raw[2])],
                            dtype="f4",
                        )
                    except Exception:
                        return np.zeros((3,), dtype="f4")

                def _joint_world_translation(index: int) -> NDArray:
                    idx = int(index)
                    if idx in cached_world:
                        return cached_world[idx]
                    if idx in resolving:
                        return _joint_local_translation(idx)
                    resolving.add(idx)
                    local = _joint_local_translation(idx)
                    try:
                        raw_parent = getattr(joints[idx], "parent_index", -1)
                        parent_idx = int(raw_parent) if raw_parent is not None else -1
                    except Exception:
                        parent_idx = -1
                    if 0 <= parent_idx < len(joints) and parent_idx != idx:
                        world = (
                            _joint_world_translation(parent_idx) + local
                        ).astype("f4", copy=False)
                    else:
                        world = local
                    cached_world[idx] = world
                    resolving.discard(idx)
                    return world

                rows: List[Tuple[float, float, float]] = []
                for idx in range(len(joints)):
                    w = _joint_world_translation(idx)
                    rows.append((float(w[0]), float(w[1]), float(w[2])))
                if rows:
                    try:
                        joint_positions = np.asarray(rows, dtype="f4").reshape(-1, 3)
                    except Exception:
                        joint_positions = np.zeros((0, 3), dtype="f4")

        if line_points.size == 0:
            joints = list(getattr(skeleton, "joints", []) or [])
            if joints and joint_positions.size:
                segment_rows: List[NDArray] = []
                count = min(len(joints), int(joint_positions.shape[0]))
                for idx in range(count):
                    try:
                        raw_parent = getattr(joints[idx], "parent_index", -1)
                        parent_idx = int(raw_parent) if raw_parent is not None else -1
                    except Exception:
                        parent_idx = -1
                    if parent_idx < 0 or parent_idx >= count or parent_idx == idx:
                        continue
                    p0 = joint_positions[parent_idx]
                    p1 = joint_positions[idx]
                    try:
                        if float(np.linalg.norm(p1 - p0)) <= 1.0e-7:
                            continue
                    except Exception:
                        pass
                    segment_rows.append(p0)
                    segment_rows.append(p1)
                if segment_rows:
                    try:
                        line_points = np.asarray(segment_rows, dtype="f4").reshape(-1, 3)
                        if line_points.size and not _segments_all_degenerate(line_points):
                            line_source = "hierarchy_segments"
                        elif line_points.size:
                            line_points = np.zeros((0, 3), dtype="f4")
                    except Exception:
                        line_points = np.zeros((0, 3), dtype="f4")

        if line_points.size and (
            _segments_all_degenerate(line_points)
            or _segments_all_exact_degenerate(line_points)
        ):
            line_points = np.zeros((0, 3), dtype="f4")
            if line_source in {"stage6_eval", "hierarchy_segments"}:
                line_source = f"{line_source}_degenerate"

        if (
            line_points.size == 0
            and joint_positions.size
            and (not _positions_collapsed(joint_positions))
        ):
            try:
                mins = joint_positions.min(axis=0)
                maxs = joint_positions.max(axis=0)
                diag = float(np.linalg.norm(maxs - mins))
            except Exception:
                diag = 0.0
            marker = max(0.01, float(diag) * 0.01)
            if not math.isfinite(marker) or marker <= 0.0:
                marker = 0.05
            cross_rows: List[Tuple[float, float, float]] = []
            for pos in joint_positions:
                px = float(pos[0])
                py = float(pos[1])
                pz = float(pos[2])
                cross_rows.extend(
                    [
                        (px - marker, py, pz),
                        (px + marker, py, pz),
                        (px, py - marker, pz),
                        (px, py + marker, pz),
                        (px, py, pz - marker),
                        (px, py, pz + marker),
                    ]
                )
            if cross_rows:
                try:
                    line_points = np.asarray(cross_rows, dtype="f4").reshape(-1, 3)
                    if line_points.size and not _segments_all_degenerate(line_points):
                        line_source = "joint_cross_markers"
                    elif line_points.size:
                        line_points = np.zeros((0, 3), dtype="f4")
                except Exception:
                    line_points = np.zeros((0, 3), dtype="f4")

        if line_points.size == 0:
            try:
                skeleton_name = str(getattr(skeleton, "name", "") or "<unnamed>")
                joint_count = int(len(list(getattr(skeleton, "joints", []) or [])))
                clip_name = str(getattr(clip, "name", "") or "<bind>")
                self._mgl_fbx_joints_log_throttled(
                    f"_mgl_fbx_joint_line_points_empty_{id(skeleton)}_{clip_name}_{int(bool(loop))}",
                    "joint-line-points empty "
                    + f"skeleton={skeleton_name} joints={joint_count} clip={clip_name} "
                    + f"sample={float(sample_time):.6f} loop={bool(loop)} source={line_source} "
                    + f"stage6_segments={int(line_from_stage6.shape[0] // 2) if line_from_stage6.size else 0}",
                    interval=0.5,
                )
            except Exception:
                pass
            return np.zeros((0, 3), dtype="f4")
        output = np.asarray(line_points, dtype="f4").reshape(-1, 3)
        try:
            skeleton_name = str(getattr(skeleton, "name", "") or "<unnamed>")
            clip_name = str(getattr(clip, "name", "") or "<bind>")
            seg_count = int(output.shape[0] // 2)
            joint_count = int(joint_positions.shape[0]) if joint_positions.size else int(
                len(list(getattr(skeleton, "joints", []) or []))
            )
            self._mgl_fbx_joints_log_throttled(
                f"_mgl_fbx_joint_line_points_ok_{id(skeleton)}_{clip_name}_{int(bool(loop))}",
                "joint-line-points ok "
                + f"skeleton={skeleton_name} joints={joint_count} clip={clip_name} "
                + f"sample={float(sample_time):.6f} loop={bool(loop)} source={line_source} segments={seg_count}",
                interval=0.5,
            )
        except Exception:
            pass
        return output

    @staticmethod
    def _mgl_fbx_joint_debug_color(joint_index: int) -> Tuple[float, float, float]:
        idx = int(max(0, joint_index)) + 1
        # Deterministic pseudo-random color per joint index.
        r = 0.20 + 0.80 * (abs(math.sin(float(idx) * 12.9898 + 78.233)) % 1.0)
        g = 0.20 + 0.80 * (abs(math.sin(float(idx) * 39.3468 + 11.135)) % 1.0)
        b = 0.20 + 0.80 * (abs(math.sin(float(idx) * 73.1563 + 47.853)) % 1.0)
        return (float(r), float(g), float(b))

    def _mgl_fbx_weight_debug_vertex_colors(
        self,
        joint_indices: NDArray,
        joint_weights: NDArray,
        joint_count: int,
    ) -> NDArray:
        if np is None:
            raise RuntimeError("numpy unavailable")
        if (
            joint_indices.ndim != 2
            or joint_weights.ndim != 2
            or joint_indices.shape != joint_weights.shape
        ):
            return np.zeros((0, 3), dtype="f4")
        vertex_count = int(joint_indices.shape[0])
        if vertex_count <= 0:
            return np.zeros((0, 3), dtype="f4")

        palette_count = max(1, int(joint_count))
        palette = np.zeros((palette_count, 3), dtype="f4")
        for joint_idx in range(palette_count):
            palette[joint_idx] = np.array(self._mgl_fbx_joint_debug_color(joint_idx), dtype="f4")

        colors = np.zeros((vertex_count, 3), dtype="f4")
        weighted_mask = np.zeros((vertex_count,), dtype=bool)
        slots = int(joint_indices.shape[1])
        for slot in range(slots):
            js = joint_indices[:, slot]
            ws = joint_weights[:, slot]
            valid = (js >= 0) & (js < palette_count) & (ws > 1.0e-8)
            if not np.any(valid):
                continue
            colors[valid] += palette[js[valid]] * ws[valid, None]
            weighted_mask[valid] = True
        if np.any(~weighted_mask):
            colors[~weighted_mask] = np.array((0.12, 0.12, 0.12), dtype="f4")
        return colors.astype("f4", copy=False)

    def _mgl_mesh_entry_reset_vertex_colors(self, entry: Dict[str, Any]) -> None:
        if np is None:
            return
        cbo = entry.get("cbo")
        points = entry.get("points")
        if cbo is None or points is None:
            return
        try:
            count = int(np.asarray(points).reshape(-1, 3).shape[0])
        except Exception:
            return
        if count <= 0:
            return
        rgba = np.ones((count, 4), dtype="f4")
        try:
            cbo.write(rgba.tobytes())
        except Exception:
            pass

    def _mgl_fbx_apply_weight_debug_colors(
        self,
        entry: Dict[str, Any],
        spec: Dict[str, Any],
        joint_count: int,
    ) -> None:
        if np is None:
            return
        cbo = entry.get("cbo")
        if cbo is None:
            return
        try:
            triangle_indices = np.asarray(spec.get("triangle_indices"), dtype=np.int64).ravel()
            joint_indices = np.asarray(spec.get("joint_indices"), dtype=np.int32)
            joint_weights = np.asarray(spec.get("joint_weights"), dtype="f4")
        except Exception:
            return
        if triangle_indices.size == 0 or joint_indices.size == 0 or joint_weights.size == 0:
            return
        vertex_rgb = self._mgl_fbx_weight_debug_vertex_colors(joint_indices, joint_weights, joint_count)
        if vertex_rgb.size == 0:
            return
        try:
            tri_rgb = vertex_rgb[triangle_indices].reshape(-1, 3).astype("f4", copy=False)
        except Exception:
            return
        tri_rgba = np.ones((tri_rgb.shape[0], 4), dtype="f4")
        tri_rgba[:, :3] = tri_rgb
        try:
            cbo.write(tri_rgba.tobytes())
        except Exception:
            pass

    @staticmethod
    def _mgl_fbx_transform_points_row_major(mats: NDArray, points: NDArray) -> NDArray:
        x = points[:, 0]
        y = points[:, 1]
        z = points[:, 2]
        out = np.empty_like(points, dtype="f4")
        out[:, 0] = (mats[:, 0, 0] * x) + (mats[:, 0, 1] * y) + (mats[:, 0, 2] * z) + mats[:, 0, 3]
        out[:, 1] = (mats[:, 1, 0] * x) + (mats[:, 1, 1] * y) + (mats[:, 1, 2] * z) + mats[:, 1, 3]
        out[:, 2] = (mats[:, 2, 0] * x) + (mats[:, 2, 1] * y) + (mats[:, 2, 2] * z) + mats[:, 2, 3]
        return out

    @staticmethod
    def _mgl_fbx_apply_affine_row_major(matrix: NDArray, points: NDArray) -> NDArray:
        mat = np.asarray(matrix, dtype="f4").reshape(4, 4)
        pts = np.asarray(points, dtype="f4").reshape(-1, 3)
        x = pts[:, 0]
        y = pts[:, 1]
        z = pts[:, 2]
        out = np.empty_like(pts, dtype="f4")
        out[:, 0] = (mat[0, 0] * x) + (mat[0, 1] * y) + (mat[0, 2] * z) + mat[0, 3]
        out[:, 1] = (mat[1, 0] * x) + (mat[1, 1] * y) + (mat[1, 2] * z) + mat[1, 3]
        out[:, 2] = (mat[2, 0] * x) + (mat[2, 1] * y) + (mat[2, 2] * z) + mat[2, 3]
        return out

    @staticmethod
    def _mgl_fbx_fit_affine_row_major(src_points: NDArray, dst_points: NDArray) -> NDArray:
        src = np.asarray(src_points, dtype="f4").reshape(-1, 3)
        dst = np.asarray(dst_points, dtype="f4").reshape(-1, 3)
        count = int(min(src.shape[0], dst.shape[0]))
        if count < 4:
            return np.eye(4, dtype="f4")
        src = src[:count]
        dst = dst[:count]
        ones = np.ones((count, 1), dtype="f4")
        lhs = np.concatenate((src, ones), axis=1)
        try:
            # lhs @ coeff = dst, where coeff shape is (4,3)
            coeff, _, _, _ = np.linalg.lstsq(lhs, dst, rcond=None)
        except Exception:
            return np.eye(4, dtype="f4")
        mat = np.eye(4, dtype="f4")
        # Convert row-vector coeff form to the row-major matrix used by shader-side convention.
        mat[0, 0] = float(coeff[0, 0])
        mat[0, 1] = float(coeff[1, 0])
        mat[0, 2] = float(coeff[2, 0])
        mat[0, 3] = float(coeff[3, 0])
        mat[1, 0] = float(coeff[0, 1])
        mat[1, 1] = float(coeff[1, 1])
        mat[1, 2] = float(coeff[2, 1])
        mat[1, 3] = float(coeff[3, 1])
        mat[2, 0] = float(coeff[0, 2])
        mat[2, 1] = float(coeff[1, 2])
        mat[2, 2] = float(coeff[2, 2])
        mat[2, 3] = float(coeff[3, 2])
        return mat

    def _mgl_prepare_fbx_rig_mesh_item(self, item: MGLSceneItem) -> None:
        if np is None:
            return
        payload = item.payload or {}
        context = payload.get("fbx_rig_context")
        context_dict = context if isinstance(context, dict) else None
        skinning_enabled = self._mgl_fbx_mesh_skinning_enabled(context_dict)
        weight_debug = self._mgl_fbx_skin_weight_debug_enabled(context_dict)
        settings_sig = (1 if skinning_enabled else 0, 1 if weight_debug else 0)
        if payload.get("_fbx_skin_settings_sig", None) != settings_sig:
            payload.pop("_fbx_skin_runtime", None)
            payload.pop("_fbx_skin_frame", None)
            payload.pop("_fbx_skin_weight_colors_applied", None)
            payload["_fbx_skin_prepare_done"] = False
        payload["_fbx_skin_settings_sig"] = settings_sig
        context_sig = None
        if isinstance(context_dict, dict):
            meshes_obj = context_dict.get("meshes")
            try:
                mesh_count = len(list(meshes_obj or []))
            except Exception:
                mesh_count = 0
            context_sig = (
                id(context_dict.get("skeleton")),
                id(context_dict.get("clip")),
                id(meshes_obj),
                int(mesh_count),
                bool(context_dict.get("retarget_result", False)),
                str(context_dict.get("retarget_clip_name") or ""),
                int(context_dict.get("retarget_track_count") or 0),
                int(context_dict.get("retarget_target_pose_offset_count") or 0),
                bool(context_dict.get("retarget_target_pose_rebind_inverse_bind", True)),
            )
        if payload.get("_fbx_skin_context_sig", None) != context_sig:
            payload.pop("_fbx_skin_runtime", None)
            payload.pop("_fbx_skin_frame", None)
            payload.pop("_fbx_skin_weight_colors_applied", None)
            payload["_fbx_skin_prepare_done"] = False
        payload["_fbx_skin_context_sig"] = context_sig
        payload["_fbx_skin_weight_debug"] = bool(weight_debug)

        if not skinning_enabled:
            runtime = payload.get("_fbx_skin_runtime")
            if isinstance(runtime, dict):
                for mesh in list(runtime.get("meshes") or []):
                    try:
                        bind_positions = np.asarray(mesh.get("bind_positions"), dtype="f4").reshape(-1, 3)
                        triangle_indices = np.asarray(mesh.get("triangle_indices"), dtype=np.int64).ravel()
                        if bind_positions.size == 0 or triangle_indices.size == 0:
                            continue
                        tri_points = bind_positions[triangle_indices].reshape(-1, 3).astype("f4", copy=False)
                        affine = mesh.get("affine")
                        if affine is not None:
                            tri_points = self._mgl_fbx_apply_affine_row_major(affine, tri_points)
                        tri_normals = self._mgl_fbx_triangle_normals(tri_points)
                        vbo = mesh.get("vbo")
                        nbo = mesh.get("nbo")
                        if vbo is not None:
                            vbo.write(tri_points.tobytes())
                        if nbo is not None:
                            nbo.write(tri_normals.tobytes())
                    except Exception:
                        continue
            payload.pop("_fbx_skin_runtime", None)
            payload.pop("_fbx_skin_frame", None)
            if not weight_debug:
                for entry in [sub for sub in list(payload.get("submeshes") or []) if isinstance(sub, dict)]:
                    self._mgl_mesh_entry_reset_vertex_colors(entry)
                if payload.get("cbo") is not None and payload.get("points") is not None:
                    self._mgl_mesh_entry_reset_vertex_colors(payload)
                payload["_fbx_skin_prepare_done"] = True
                payload["_fbx_skin_weight_colors_applied"] = False
                item.payload = payload
                return
        if bool(payload.get("_fbx_skin_prepare_done", False)):
            if isinstance(payload.get("_fbx_skin_runtime"), dict):
                return
            if bool(payload.get("_fbx_skin_weight_colors_applied", False)):
                return
            context_check = payload.get("fbx_rig_context")
            if not isinstance(context_check, dict) or not list(context_check.get("meshes") or []):
                return
        payload["_fbx_skin_prepare_done"] = True

        skeleton = context.get("skeleton") if isinstance(context, dict) else None
        if skeleton is None:
            item.payload = payload
            return
        specs = self._mgl_fbx_skin_specs_from_context(context)
        if not specs:
            item.payload = payload
            return

        # Preferred path: keep the original loaded mesh buffers (and UVs/material layout) and
        # drive only vertex positions/normals from the rig evaluator.
        existing_submeshes = [sub for sub in list(payload.get("submeshes") or []) if isinstance(sub, dict)]
        if not weight_debug:
            for entry in existing_submeshes:
                self._mgl_mesh_entry_reset_vertex_colors(entry)
        runtime_meshes: List[Dict[str, Any]] = []
        joint_count = int(len(list(getattr(skeleton, "joints", []) or [])))
        specs_by_name: Dict[str, Dict[str, Any]] = {}
        for spec in specs:
            spec_name = str(spec.get("name", "") or "").strip().lower()
            if spec_name and spec_name not in specs_by_name:
                specs_by_name[spec_name] = spec
        used_spec_keys: set[str] = set()
        index_fallback: List[Dict[str, Any]] = [spec for spec in specs]
        fallback_idx = 0

        for entry in existing_submeshes:
            entry_name_key = str(entry.get("name", "") or "").strip().lower()
            spec = None
            if entry_name_key:
                candidate = specs_by_name.get(entry_name_key)
                if candidate is not None and entry_name_key not in used_spec_keys:
                    spec = candidate
                    used_spec_keys.add(entry_name_key)
            if spec is None:
                while fallback_idx < len(index_fallback):
                    candidate = index_fallback[fallback_idx]
                    fallback_idx += 1
                    candidate_key = str(candidate.get("name", "") or "").strip().lower()
                    if candidate_key and candidate_key in used_spec_keys:
                        continue
                    if candidate_key:
                        used_spec_keys.add(candidate_key)
                    spec = candidate
                    break
            if spec is None:
                continue

            bind_positions = np.asarray(spec.get("bind_positions"), dtype="f4").reshape(-1, 3)
            triangle_indices = np.asarray(spec.get("triangle_indices"), dtype=np.int64).ravel()
            if bind_positions.size == 0 or triangle_indices.size == 0:
                continue
            render_points_raw = entry.get("points")
            if render_points_raw is None:
                continue
            render_points = np.asarray(render_points_raw, dtype="f4").reshape(-1, 3)
            bind_tri_points = np.asarray(spec.get("bind_tri_points"), dtype="f4").reshape(-1, 3)
            if render_points.shape[0] != bind_tri_points.shape[0]:
                continue
            if entry.get("vbo") is None or entry.get("nbo") is None:
                continue
            affine = self._mgl_fbx_fit_affine_row_major(bind_tri_points, render_points)
            try:
                fit_points = self._mgl_fbx_apply_affine_row_major(affine, bind_tri_points)
                delta = fit_points - render_points
                rmse = float(np.sqrt(np.mean(np.sum(delta * delta, axis=1))))
                bbox_min = render_points.min(axis=0)
                bbox_max = render_points.max(axis=0)
                bbox_diag = float(np.linalg.norm(bbox_max - bbox_min))
                normalized_rmse = rmse / max(1.0e-5, bbox_diag)
            except Exception:
                rmse = 0.0
                normalized_rmse = 0.0
            # Guard against invalid submesh pairing to avoid catastrophic scrambling.
            if normalized_rmse > 0.05:
                continue
            if weight_debug:
                self._mgl_fbx_apply_weight_debug_colors(entry, spec, joint_count)
            if skinning_enabled:
                spec_joint_indices = np.asarray(spec.get("joint_indices"), dtype=np.int32)
                spec_joint_weights = np.asarray(spec.get("joint_weights"), dtype="f4")
                influence_slots, unweighted_indices = self._mgl_fbx_runtime_influence_slots(
                    spec_joint_indices,
                    spec_joint_weights,
                    joint_count,
                )
                runtime_meshes.append(
                    {
                        "bind_positions": bind_positions,
                        "triangle_indices": triangle_indices,
                        "joint_indices": spec_joint_indices,
                        "joint_weights": spec_joint_weights,
                        "influence_slots": influence_slots,
                        "unweighted_indices": unweighted_indices,
                        "vbo": entry.get("vbo"),
                        "nbo": entry.get("nbo"),
                        "affine": affine,
                        "fit_rmse": float(rmse),
                    }
                )
        if skinning_enabled and runtime_meshes:
            payload["_fbx_skin_runtime"] = {
                "skeleton": skeleton,
                "clip": context.get("clip"),
                "loop": bool(context.get("loop", True)),
                "meshes": runtime_meshes,
            }
            payload["_fbx_skin_frame"] = None
            payload["_fbx_skin_weight_colors_applied"] = bool(weight_debug)
            item.payload = payload
            return

        # If source mesh buffers exist but mapping failed, keep original geometry untouched
        # rather than swapping in a canonical fallback that can alter UV/material layout.
        if existing_submeshes:
            payload["_fbx_skin_weight_colors_applied"] = bool(weight_debug)
            item.payload = payload
            return

        # Fallback path for runtimes where we cannot keep source submesh buffers
        # (for example, loader could not provide usable render points).
        old_resources = list(item.resources or [])
        old_submeshes = existing_submeshes
        old_texture = payload.get("texture")
        fallback_texture = old_texture
        if fallback_texture is None:
            for sub in old_submeshes:
                tex = sub.get("texture") if isinstance(sub, dict) else None
                if tex is not None:
                    fallback_texture = tex
                    break

        source_color = payload.get("color")
        if source_color is None:
            for sub in old_submeshes:
                if isinstance(sub, dict) and sub.get("color") is not None:
                    source_color = sub.get("color")
                    break
        if source_color is None:
            source_color = self._mgl_mesh_color

        entries: List[Dict[str, Any]] = []
        fallback_runtime_meshes: List[Dict[str, Any]] = []
        for spec in specs:
            tri_points = np.asarray(spec["bind_tri_points"], dtype="f4").reshape(-1, 3)
            tri_normals = np.asarray(spec["bind_tri_normals"], dtype="f4").reshape(-1, 3)
            tri_uvs = np.zeros((tri_points.shape[0], 2), dtype="f4")
            entry = self._mgl_build_mesh_entry(tri_points, tri_normals, tri_uvs)
            if entry is None:
                continue
            if weight_debug:
                self._mgl_fbx_apply_weight_debug_colors(entry, spec, joint_count)
            entry["texture"] = fallback_texture
            entry["color"] = source_color
            entries.append(entry)
            if skinning_enabled:
                spec_joint_indices = np.asarray(spec["joint_indices"], dtype=np.int32)
                spec_joint_weights = np.asarray(spec["joint_weights"], dtype="f4")
                influence_slots, unweighted_indices = self._mgl_fbx_runtime_influence_slots(
                    spec_joint_indices,
                    spec_joint_weights,
                    joint_count,
                )
                fallback_runtime_meshes.append(
                    {
                        "bind_positions": np.asarray(spec["bind_positions"], dtype="f4"),
                        "triangle_indices": np.asarray(spec["triangle_indices"], dtype=np.int64),
                        "joint_indices": spec_joint_indices,
                        "joint_weights": spec_joint_weights,
                        "influence_slots": influence_slots,
                        "unweighted_indices": unweighted_indices,
                        "vbo": entry.get("vbo"),
                        "nbo": entry.get("nbo"),
                        "affine": np.eye(4, dtype="f4"),
                    }
                )
        if not entries:
            item.payload = payload
            return

        payload.pop("vao", None)
        payload.pop("texture", None)
        payload["submeshes"] = entries
        if skinning_enabled:
            payload["_fbx_skin_runtime"] = {
                "skeleton": skeleton,
                "clip": context.get("clip"),
                "loop": bool(context.get("loop", True)),
                "meshes": fallback_runtime_meshes,
            }
            payload["_fbx_skin_frame"] = None
        else:
            payload.pop("_fbx_skin_runtime", None)
            payload.pop("_fbx_skin_frame", None)
        payload["_fbx_skin_weight_colors_applied"] = bool(weight_debug)
        item.payload = payload

        resources: List[object] = []
        seen_resource: set[int] = set()
        for entry in entries:
            for res in (
                entry.get("vao"),
                entry.get("vbo"),
                entry.get("nbo"),
                entry.get("tbo"),
                entry.get("cbo"),
                entry.get("ibo"),
                entry.get("texture"),
            ):
                if res is None:
                    continue
                rid = id(res)
                if rid in seen_resource:
                    continue
                seen_resource.add(rid)
                resources.append(res)
        item.resources = resources

        reused = {id(res) for res in resources}
        for res in old_resources:
            if res is None or id(res) in reused or not hasattr(res, "release"):
                continue
            try:
                res.release()
            except Exception:
                pass

        owner = str(payload.get("owner") or "").strip()
        if owner:
            try:
                proc_entry = getattr(self, "_mgl_scene_proc_textures_by_owner", {}).get(owner)
            except Exception:
                proc_entry = None
            if isinstance(proc_entry, dict):
                proc_entry["subs"] = entries

    def _mgl_refresh_fbx_rig_mesh_item(self, item: MGLSceneItem) -> None:
        if np is None:
            return
        self._mgl_prepare_fbx_rig_mesh_item(item)
        payload = item.payload or {}
        runtime = payload.get("_fbx_skin_runtime")
        if not isinstance(runtime, dict):
            return

        skeleton = runtime.get("skeleton")
        if skeleton is None:
            return
        clip = runtime.get("clip")
        loop = bool(runtime.get("loop", True))
        context = payload.get("fbx_rig_context")
        frame = self._mgl_timeline_frame_index()
        sample_seconds = self._mgl_fbx_context_timeline_sample_seconds(
            context if isinstance(context, dict) else None,
            payload.get("owner"),
        )
        skin_frame_key = (int(frame), round(float(sample_seconds), 6))
        if payload.get("_fbx_skin_frame", None) == skin_frame_key:
            return
        try:
            evaluation = evaluate_rig_at_time(
                skeleton,
                clip,
                sample_seconds,
                loop=loop,
                include_debug_data=False,
            )
        except Exception:
            return

        try:
            skin_mats = np.asarray(evaluation.skin_matrices, dtype="f4").reshape(-1, 4, 4)
        except Exception:
            return
        joint_count = int(skin_mats.shape[0])
        meshes = list(runtime.get("meshes") or [])
        for mesh in meshes:
            bind_positions = np.asarray(mesh.get("bind_positions"), dtype="f4").reshape(-1, 3)
            triangle_indices = np.asarray(mesh.get("triangle_indices"), dtype=np.int64).ravel()
            joint_indices = np.asarray(mesh.get("joint_indices"), dtype=np.int32)
            joint_weights = np.asarray(mesh.get("joint_weights"), dtype="f4")
            if bind_positions.size == 0 or triangle_indices.size == 0:
                continue

            vertex_count = int(bind_positions.shape[0])
            deformed = bind_positions.copy()
            influence_slots = list(mesh.get("influence_slots") or [])
            unweighted_indices = np.asarray(mesh.get("unweighted_indices"), dtype=np.int64).ravel()
            if influence_slots and joint_count > 0:
                deformed = np.zeros_like(bind_positions, dtype="f4")
                for slot in influence_slots:
                    vids = np.asarray(slot.get("vertex_indices"), dtype=np.int64).ravel()
                    js = np.asarray(slot.get("joint_indices"), dtype=np.int32).ravel()
                    ws = np.asarray(slot.get("weights"), dtype="f4").ravel()
                    if vids.size == 0 or js.size == 0 or ws.size == 0:
                        continue
                    valid = (js >= 0) & (js < joint_count) & (ws > 1.0e-8)
                    if not np.any(valid):
                        continue
                    if np.count_nonzero(valid) != vids.size:
                        vids = vids[valid]
                        js = js[valid]
                        ws = ws[valid]
                    transformed = self._mgl_fbx_transform_points_row_major(skin_mats[js], bind_positions[vids])
                    deformed[vids] += transformed * ws[:, None]
                if unweighted_indices.size:
                    deformed[unweighted_indices] = bind_positions[unweighted_indices]
            elif (
                joint_indices.ndim == 2
                and joint_weights.ndim == 2
                and joint_indices.shape[0] == vertex_count
                and joint_weights.shape == joint_indices.shape
                and joint_count > 0
            ):
                deformed = np.zeros_like(bind_positions, dtype="f4")
                weighted_mask = np.zeros((vertex_count,), dtype=bool)
                slots = int(joint_indices.shape[1])
                for slot in range(slots):
                    js = joint_indices[:, slot]
                    ws = joint_weights[:, slot]
                    valid = (js >= 0) & (js < joint_count) & (ws > 1.0e-8)
                    if not np.any(valid):
                        continue
                    transformed = self._mgl_fbx_transform_points_row_major(skin_mats[js[valid]], bind_positions[valid])
                    deformed[valid] += transformed * ws[valid, None]
                    weighted_mask[valid] = True
                if np.any(~weighted_mask):
                    deformed[~weighted_mask] = bind_positions[~weighted_mask]

            tri_points = deformed[triangle_indices].reshape(-1, 3).astype("f4", copy=False)
            affine = mesh.get("affine")
            if affine is not None:
                try:
                    tri_points = self._mgl_fbx_apply_affine_row_major(affine, tri_points)
                except Exception:
                    pass
            tri_normals = self._mgl_fbx_triangle_normals(tri_points)
            vbo = mesh.get("vbo")
            nbo = mesh.get("nbo")
            try:
                if vbo is not None:
                    vbo.write(tri_points.tobytes())
                if nbo is not None:
                    nbo.write(tri_normals.tobytes())
            except Exception:
                continue

            payload["_fbx_skin_frame"] = skin_frame_key
        item.payload = payload

    def _mgl_normalize_music_effects_config(self, raw) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        def _bool(value, default: bool) -> bool:
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "on", "y"}:
                    return True
                if text in {"0", "false", "no", "off", "n"}:
                    return False
            if value is None:
                return bool(default)
            return bool(value)

        def _float(value, default: float, minimum: float, maximum: float) -> float:
            try:
                parsed = float(value)
            except Exception:
                parsed = float(default)
            return max(float(minimum), min(float(maximum), float(parsed)))

        analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
        mesh = raw.get("mesh") if isinstance(raw.get("mesh"), dict) else {}
        return {
            "enabled": _bool(raw.get("enabled"), True),
            "cache_path": str(raw.get("analysis_cache_path") or "").strip(),
            "gain": _float(analysis.get("gain"), 1.0, 0.0, 8.0),
            "threshold": _float(analysis.get("threshold"), 0.05, 0.0, 0.95),
            "offset_s": _float(analysis.get("audio_start_offset_ms"), 0.0, -600000.0, 600000.0)
            / 1000.0,
            "displacement": _float(mesh.get("displacement"), 0.15, 0.0, 1000.0),
            "max_displacement": _float(mesh.get("max_displacement"), 1.0, 0.0, 1000.0),
            "outward_only": _bool(mesh.get("outward_only"), True),
        }

    def _mgl_splat_fx_from_music_effects(self, raw) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
        return {
            "schema": "qubit.splat_fx.from_music_effects.v1",
            "enabled": raw.get("enabled", True),
            "audio_path": str(raw.get("audio_path") or ""),
            "analysis_cache_path": str(raw.get("analysis_cache_path") or ""),
            "analysis": dict(analysis),
            "glow": {
                "intensity": 1.15,
                "radius_boost": 0.28,
                "saturation": 0.42,
                "wave_strength": 0.55,
                "wave_width": 0.34,
                "wave_speed": 1.0,
                "wave_axis": "y",
            },
        }

    def _mgl_normalize_splat_fx_config(self, raw) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        def _bool(value, default: bool) -> bool:
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "on", "y"}:
                    return True
                if text in {"0", "false", "no", "off", "n"}:
                    return False
            if value is None:
                return bool(default)
            return bool(value)

        def _float(value, default: float, minimum: float, maximum: float) -> float:
            try:
                parsed = float(value)
            except Exception:
                parsed = float(default)
            return max(float(minimum), min(float(maximum), float(parsed)))

        analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
        glow = raw.get("glow") if isinstance(raw.get("glow"), dict) else {}
        axis = str(glow.get("wave_axis") or raw.get("wave_axis") or "y").strip().lower()
        if axis not in {"x", "y", "z"}:
            axis = "y"
        return {
            "enabled": _bool(raw.get("enabled"), True),
            "cache_path": str(raw.get("analysis_cache_path") or "").strip(),
            "gain": _float(analysis.get("gain"), 1.25, 0.0, 8.0),
            "threshold": _float(analysis.get("threshold"), 0.04, 0.0, 0.95),
            "offset_s": _float(analysis.get("audio_start_offset_ms"), 0.0, -600000.0, 600000.0)
            / 1000.0,
            "intensity": _float(glow.get("intensity"), 1.25, 0.0, 8.0),
            "radius_boost": _float(glow.get("radius_boost"), 0.35, 0.0, 4.0),
            "saturation": _float(glow.get("saturation"), 0.45, 0.0, 4.0),
            "wave_strength": _float(glow.get("wave_strength"), 0.65, 0.0, 1.0),
            "wave_width": _float(glow.get("wave_width"), 0.32, 0.01, 2.0),
            "wave_speed": _float(glow.get("wave_speed"), 1.0, 0.0, 8.0),
            "wave_axis": axis,
        }

    def _mgl_splat_fx_music_values(self, proxy: dict, splats: NDArray, frame: int) -> Optional[NDArray]:
        if np is None or not isinstance(proxy, dict):
            return None
        if not self._mgl_timeline_fx_enabled():
            return None
        render_proxy = proxy.get("render_proxy") if isinstance(proxy.get("render_proxy"), dict) else {}
        cfg = self._mgl_normalize_splat_fx_config(render_proxy.get("splat_fx"))
        if cfg is None or not bool(cfg.get("enabled", True)):
            return None
        cache_path = str(cfg.get("cache_path") or "").strip()
        if not cache_path:
            return None
        curve = self._mgl_music_effect_curve(proxy, cache_path)
        if not isinstance(curve, dict):
            return None
        try:
            source = np.asarray(splats, dtype="f4").reshape(-1, 15)
        except Exception:
            return None
        count = int(source.shape[0])
        if count <= 0:
            return np.zeros((0, 1), dtype=np.float32)
        times = np.asarray(curve.get("times"), dtype="f4").reshape(-1)
        beat = np.asarray(curve.get("beat"), dtype="f4").reshape(-1)
        if times.size == 0 or beat.size == 0:
            return None
        time_s = float(self._mgl_timeline_time_seconds()) - float(cfg.get("offset_s", 0.0))
        if time_s < float(times[0]) or time_s > float(times[-1]):
            raw_strength = 0.0
        else:
            raw_strength = float(np.interp(time_s, times, beat))
        threshold = float(cfg.get("threshold", 0.04))
        if raw_strength <= threshold:
            strength = 0.0
        else:
            strength = (raw_strength - threshold) / max(1.0e-6, 1.0 - threshold)
        strength = max(0.0, min(1.0, strength * float(cfg.get("gain", 1.25))))
        if strength <= 1.0e-5:
            return np.zeros((count, 1), dtype=np.float32)

        axis_idx = {"x": 0, "y": 1, "z": 2}.get(str(cfg.get("wave_axis") or "y"), 1)
        coord = source[:, axis_idx].astype(np.float32, copy=False)
        cmin = float(np.min(coord))
        cmax = float(np.max(coord))
        if abs(cmax - cmin) <= 1.0e-6:
            normalized = np.zeros_like(coord, dtype=np.float32)
        else:
            normalized = (coord - np.float32(cmin)) / np.float32(cmax - cmin)
        wave_strength = float(cfg.get("wave_strength", 0.65))
        wave_width = max(0.01, float(cfg.get("wave_width", 0.32)))
        wave_speed = max(0.0, float(cfg.get("wave_speed", 1.0)))
        center = (float(time_s) * wave_speed) % 1.0 if wave_speed > 1.0e-6 else 0.5
        dist = np.abs(normalized - np.float32(center))
        dist = np.minimum(dist, np.float32(1.0) - dist)
        wave = np.exp(-((dist / np.float32(wave_width)) ** 2)).astype(np.float32, copy=False)
        values = np.float32(strength) * (
            np.float32(1.0 - wave_strength) + (np.float32(wave_strength) * wave)
        )
        values *= np.float32(float(cfg.get("intensity", 1.25)))
        return np.clip(values.reshape(-1, 1), np.float32(0.0), np.float32(4.0)).astype(np.float32, copy=False)

    def _mgl_splat_physics_glow_values(self, proxy: dict, count: int) -> Optional[NDArray]:
        if np is None or not isinstance(proxy, dict) or count <= 0:
            return None
        if not self._mgl_timeline_fx_enabled():
            return None
        config = proxy.get("splat_physics") if isinstance(proxy.get("splat_physics"), dict) else None
        if not isinstance(config, dict) or not bool(config.get("glow_enabled", False)):
            return None
        try:
            base = np.asarray(proxy.get("physics_glow_values"), dtype="f4").reshape(-1, 1)
        except Exception:
            base = np.zeros((0, 1), dtype=np.float32)
        out = np.zeros((int(count), 1), dtype=np.float32)
        if int(base.shape[0]) > 0:
            limit = min(int(count), int(base.shape[0]))
            try:
                intensity = max(0.0, min(8.0, float(config.get("glow_intensity", 1.0))))
            except Exception:
                intensity = 1.0
            out[:limit, 0] = np.clip(base[:limit, 0], np.float32(0.0), np.float32(1.0)) * np.float32(intensity)
        return np.clip(out, np.float32(0.0), np.float32(4.0)).astype(np.float32, copy=False)

    def _mgl_normalize_splat_colorize_config(self, raw) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        def _bool(value, default: bool = False) -> bool:
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "on", "y"}:
                    return True
                if text in {"0", "false", "no", "off", "n"}:
                    return False
            if value is None:
                return bool(default)
            return bool(value)

        def _float(value, default: float, minimum: float, maximum: float) -> float:
            try:
                out = float(value)
            except Exception:
                out = float(default)
            return max(float(minimum), min(float(maximum), float(out)))

        metric = str(raw.get("metric") or "thickness").strip().lower()
        if metric in {"thick", "feature", "feature_radius"}:
            metric = "thickness"
        elif metric in {"dense", "inverse_radius"}:
            metric = "density"
        elif metric not in {"thickness", "density", "radius", "area", "height_y"}:
            metric = "thickness"

        ramp = []
        for row in list(raw.get("ramp") or []):
            if not isinstance(row, dict):
                continue
            try:
                pos = _float(row.get("position"), 0.0, 0.0, 1.0)
                color = [float(v) for v in list(row.get("color") or [])[:3]]
                if len(color) != 3:
                    continue
                ramp.append(
                    {
                        "position": float(pos),
                        "color": [
                            max(0.0, min(1.0, float(color[0]))),
                            max(0.0, min(1.0, float(color[1]))),
                            max(0.0, min(1.0, float(color[2]))),
                        ],
                    }
                )
            except Exception:
                continue
        if len(ramp) < 2:
            ramp = [
                {"position": 0.0, "color": [0.1137, 0.3059, 0.8471]},
                {"position": 0.5, "color": [0.0784, 0.7216, 0.6510]},
                {"position": 1.0, "color": [0.9608, 0.6196, 0.0431]},
            ]
        ramp.sort(key=lambda item: float(item.get("position", 0.0)))
        if float(ramp[0].get("position", 0.0)) > 0.0:
            ramp.insert(0, {"position": 0.0, "color": list(ramp[0].get("color") or [1.0, 1.0, 1.0])[:3]})
        if float(ramp[-1].get("position", 1.0)) < 1.0:
            ramp.append({"position": 1.0, "color": list(ramp[-1].get("color") or [1.0, 1.0, 1.0])[:3]})

        normalize = raw.get("normalize") if isinstance(raw.get("normalize"), dict) else {}
        low_pct = _float(normalize.get("low_percentile"), 2.0, 0.0, 99.0)
        high_pct = _float(normalize.get("high_percentile"), 98.0, 1.0, 100.0)
        if high_pct <= low_pct:
            high_pct = min(100.0, low_pct + 1.0)
        return {
            "enabled": _bool(raw.get("enabled"), True),
            "metric": metric,
            "ramp": ramp,
            "low_percentile": low_pct,
            "high_percentile": high_pct,
            "invert": _bool(normalize.get("invert"), False),
            "gamma": _float(normalize.get("gamma"), 1.0, 0.05, 8.0),
            "blend": _float(raw.get("blend"), 1.0, 0.0, 1.0),
        }

    def _mgl_splat_colorize_metric_values(self, proxy: dict, splats: NDArray, metric: str) -> Optional[NDArray]:
        if np is None or not isinstance(proxy, dict):
            return None

        def _vector(name: str) -> Optional[NDArray]:
            try:
                arr = np.asarray(proxy.get(name), dtype="f4").reshape(-1)
            except Exception:
                return None
            if int(arr.shape[0]) <= 0:
                return None
            return arr.astype(np.float32, copy=False)

        values = None
        if metric == "area":
            values = _vector("source_triangle_area")
        elif metric == "height_y":
            try:
                bind = np.asarray(proxy.get("bind_positions"), dtype="f4").reshape(-1, 3)
                if int(bind.shape[0]) > 0:
                    values = bind[:, 1].astype(np.float32, copy=False)
            except Exception:
                values = None
        elif metric in {"thickness", "density"}:
            values = _vector("source_feature_radius")
            if values is None:
                try:
                    brs = np.asarray(proxy.get("bind_radius_scale"), dtype="f4").reshape(-1, 4)
                    if int(brs.shape[0]) > 0:
                        values = brs[:, 0].astype(np.float32, copy=False)
                except Exception:
                    values = None
        elif metric == "radius":
            try:
                brs = np.asarray(proxy.get("bind_radius_scale"), dtype="f4").reshape(-1, 4)
                if int(brs.shape[0]) > 0:
                    values = brs[:, 0].astype(np.float32, copy=False)
            except Exception:
                values = None

        if values is None:
            try:
                arr = np.asarray(splats, dtype="f4").reshape(-1, 15)
                values = arr[:, 7].astype(np.float32, copy=False)
            except Exception:
                return None
        if metric == "density":
            values = np.float32(1.0) / np.maximum(values.astype(np.float32, copy=False), np.float32(1.0e-8))
        return values.astype(np.float32, copy=False)

    def _mgl_apply_splat_colorize_to_splats(self, proxy: dict, splats: NDArray) -> NDArray:
        if np is None or not isinstance(proxy, dict):
            return splats
        render_proxy = proxy.get("render_proxy") if isinstance(proxy.get("render_proxy"), dict) else {}
        cfg = self._mgl_normalize_splat_colorize_config(render_proxy.get("splat_colorize"))
        if cfg is None or not bool(cfg.get("enabled", True)):
            return splats
        try:
            out = np.asarray(splats, dtype="f4").reshape(-1, 15).astype(np.float32, copy=True)
        except Exception:
            return splats
        count = int(out.shape[0])
        if count <= 0:
            return out
        values = self._mgl_splat_colorize_metric_values(proxy, out, str(cfg.get("metric") or "thickness"))
        if values is None:
            return out
        limit = min(count, int(values.shape[0]))
        if limit <= 0:
            return out
        values = values[:limit].astype(np.float32, copy=False)
        finite = np.isfinite(values)
        if not np.any(finite):
            return out
        valid = values[finite]
        try:
            lo = float(np.percentile(valid, float(cfg.get("low_percentile", 2.0))))
            hi = float(np.percentile(valid, float(cfg.get("high_percentile", 98.0))))
        except Exception:
            lo = float(np.min(valid))
            hi = float(np.max(valid))
        if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo + 1.0e-8:
            lo = float(np.min(valid))
            hi = float(np.max(valid))
        if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo + 1.0e-8:
            t = np.full((limit,), 0.5, dtype=np.float32)
        else:
            t = ((values - np.float32(lo)) / np.float32(hi - lo)).astype(np.float32, copy=False)
            t = np.clip(t, np.float32(0.0), np.float32(1.0))
        if bool(cfg.get("invert", False)):
            t = np.float32(1.0) - t
        try:
            gamma = max(0.05, min(8.0, float(cfg.get("gamma", 1.0))))
        except Exception:
            gamma = 1.0
        if abs(gamma - 1.0) > 1.0e-5:
            t = np.power(t, np.float32(gamma)).astype(np.float32, copy=False)

        ramp = list(cfg.get("ramp") or [])
        positions = np.asarray([float(row.get("position", 0.0)) for row in ramp], dtype=np.float32)
        colors = np.asarray([list(row.get("color") or [1.0, 1.0, 1.0])[:3] for row in ramp], dtype=np.float32)
        if positions.ndim != 1 or colors.ndim != 2 or colors.shape[0] != positions.shape[0] or colors.shape[1] != 3:
            return out
        rgb = np.zeros((limit, 3), dtype=np.float32)
        for channel in range(3):
            rgb[:, channel] = np.interp(t, positions, colors[:, channel]).astype(np.float32, copy=False)
        try:
            blend = max(0.0, min(1.0, float(cfg.get("blend", 1.0))))
        except Exception:
            blend = 1.0
        if blend >= 1.0 - 1.0e-6:
            out[:limit, 3:6] = np.clip(rgb, 0.0, 1.0)
        elif blend > 1.0e-6:
            out[:limit, 3:6] = np.clip(
                out[:limit, 3:6] * np.float32(1.0 - blend) + rgb * np.float32(blend),
                0.0,
                1.0,
            )
        return out.astype(np.float32, copy=False)

    def _mgl_apply_splat_fx_to_splats(
        self,
        proxy: dict,
        splats: NDArray,
        frame: int,
        *,
        apply_colorize: bool = True,
    ) -> tuple[NDArray, NDArray]:
        if np is None:
            return splats, splats
        try:
            out = np.asarray(splats, dtype="f4").reshape(-1, 15).astype(np.float32, copy=True)
        except Exception:
            return splats, np.zeros((0, 1), dtype=np.float32)
        count = int(out.shape[0])
        if count <= 0:
            return out, np.zeros((0, 1), dtype=np.float32)
        if bool(apply_colorize):
            out = self._mgl_apply_splat_colorize_to_splats(proxy, out)
        glow = np.zeros((count, 1), dtype=np.float32)
        if not self._mgl_timeline_fx_enabled():
            return out, glow
        music_values = self._mgl_splat_fx_music_values(proxy, out, int(frame))
        if music_values is not None and int(music_values.shape[0]) == count:
            glow = np.maximum(glow, music_values.astype(np.float32, copy=False))
        physics_values = self._mgl_splat_physics_glow_values(proxy, count)
        if physics_values is not None and int(physics_values.shape[0]) == count:
            glow = np.maximum(glow, physics_values.astype(np.float32, copy=False))
        glow = np.clip(glow, np.float32(0.0), np.float32(4.0)).astype(np.float32, copy=False)
        if not np.any(glow > np.float32(1.0e-5)):
            return out, glow

        render_proxy = proxy.get("render_proxy") if isinstance(proxy.get("render_proxy"), dict) else {}
        cfg = self._mgl_normalize_splat_fx_config(render_proxy.get("splat_fx")) or {}
        physics_cfg = proxy.get("splat_physics") if isinstance(proxy.get("splat_physics"), dict) else {}
        try:
            radius_boost = max(
                float(cfg.get("radius_boost", 0.0) or 0.0),
                float(physics_cfg.get("glow_radius_boost", 0.0) or 0.0),
            )
        except Exception:
            radius_boost = float(cfg.get("radius_boost", 0.0) or 0.0)
        try:
            saturation = max(0.0, float(cfg.get("saturation", 0.45) or 0.0))
        except Exception:
            saturation = 0.45

        g = glow[:, 0].astype(np.float32, copy=False)
        if radius_boost > 1.0e-6:
            out[:, 7] *= np.float32(1.0) + (g * np.float32(radius_boost))
        if saturation > 1.0e-6:
            rgb = out[:, 3:6].astype(np.float32, copy=False)
            luma = (
                rgb[:, 0:1] * np.float32(0.2126)
                + rgb[:, 1:2] * np.float32(0.7152)
                + rgb[:, 2:3] * np.float32(0.0722)
            )
            sat = np.float32(1.0) + (g[:, None] * np.float32(saturation))
            out[:, 3:6] = np.clip(luma + (rgb - luma) * sat + (g[:, None] * np.float32(0.08)), 0.0, 4.0)
        return out.astype(np.float32, copy=False), glow.astype(np.float32, copy=False)

    def _mgl_music_effect_entries(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        entry = payload.get("mesh_entry")
        if isinstance(entry, dict):
            entries.append(entry)
        for sub in list(payload.get("submeshes") or []):
            if isinstance(sub, dict):
                entries.append(sub)
        return entries

    def _mgl_music_effect_packed_copies(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        cfg = payload.get("copy_to_points")
        if not isinstance(cfg, dict) or not bool(cfg.get("pack", False)):
            return []
        if bool(cfg.get("gpu_instances", False)):
            return []
        return [row for row in list(cfg.get("copies") or []) if isinstance(row, dict)]

    def _mgl_music_effect_gpu_instance_copies(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        cfg = payload.get("copy_to_points")
        if not isinstance(cfg, dict) or not bool(cfg.get("gpu_instances", False)):
            return []
        return [row for row in list(cfg.get("copies") or []) if isinstance(row, dict)]

    def _mgl_write_copy_to_points_instance_matrices(self, payload: Dict[str, Any], matrices) -> bool:
        if np is None or not isinstance(payload, dict):
            return False
        try:
            mats = np.asarray(matrices, dtype="f4").reshape(-1, 4, 4)
        except Exception:
            return False
        buffers = payload.get("_instance_buffers")
        if not isinstance(buffers, tuple) or len(buffers) != 4:
            return False
        try:
            for idx, buf in enumerate(buffers):
                if buf is None:
                    return False
                buf.write(np.ascontiguousarray(mats[:, idx, :], dtype="f4").tobytes())
            payload["_copy_to_points_current_instance_matrices"] = mats.astype("f4", copy=True)
            try:
                self._mgl_shadow_dirty = True
                self._mgl_shadow_valid = False
                self._mgl_shadow_signature = None
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _mgl_restore_music_effects_instances(self, payload: Dict[str, Any]) -> bool:
        if np is None or not isinstance(payload, dict):
            return False
        cfg = payload.get("copy_to_points")
        if not isinstance(cfg, dict) or not bool(cfg.get("gpu_instances", False)):
            return False
        base = payload.get("_copy_to_points_base_instance_matrices")
        if base is None:
            base = self._mgl_copy_to_points_instance_matrices(cfg)
            if base is None:
                return False
            payload["_copy_to_points_base_instance_matrices"] = base.astype("f4", copy=True)
        if not bool(payload.get("_music_effects_instance_applied", False)):
            return False
        if not self._mgl_write_copy_to_points_instance_matrices(payload, base):
            return False
        payload["_music_effects_instance_applied"] = False
        return True

    def _mgl_apply_music_effects_to_gpu_instances(
        self,
        item: MGLSceneItem,
        amount: float,
    ) -> bool:
        if np is None or item is None:
            return False
        payload = item.payload or {}
        cfg = payload.get("copy_to_points")
        if not isinstance(cfg, dict) or not bool(cfg.get("gpu_instances", False)):
            return False
        base = payload.get("_copy_to_points_base_instance_matrices")
        if base is None:
            base = self._mgl_copy_to_points_instance_matrices(cfg)
            if base is None:
                return False
            payload["_copy_to_points_base_instance_matrices"] = base.astype("f4", copy=True)
        try:
            base_mats = np.asarray(base, dtype="f4").reshape(-1, 4, 4)
        except Exception:
            return False
        if base_mats.size == 0:
            return False
        copies = self._mgl_music_effect_gpu_instance_copies(payload)
        if not copies:
            return False
        deformed = base_mats.copy()
        limit = min(int(deformed.shape[0]), int(len(copies)))
        for idx in range(limit):
            copy = copies[idx]
            try:
                normal = np.asarray(copy.get("packed_normal"), dtype="f4").reshape(3)
            except Exception:
                continue
            normal_len = float(np.linalg.norm(normal))
            if normal_len <= 1.0e-6:
                continue
            normal = normal / normal_len
            deformed[idx, 3, :3] = base_mats[idx, 3, :3] + (normal * float(amount))
        if not self._mgl_write_copy_to_points_instance_matrices(payload, deformed):
            return False
        payload["_music_effects_instance_applied"] = True
        item.payload = payload
        return True

    def _mgl_restore_music_effects_mesh_item(self, item: MGLSceneItem) -> None:
        if np is None or item is None:
            return
        payload = item.payload or {}
        changed = False
        for entry in self._mgl_music_effect_entries(payload):
            base_points = entry.get("_music_effects_base_points")
            if base_points is None:
                continue
            try:
                points = np.asarray(base_points, dtype="f4").reshape(-1, 3)
            except Exception:
                continue
            try:
                vbo = entry.get("vbo")
                if vbo is not None:
                    vbo.write(points.tobytes())
                entry["points"] = points
                entry["_music_effects_applied"] = False
                changed = True
            except Exception:
                continue
        if self._mgl_restore_music_effects_instances(payload):
            changed = True
        if changed:
            payload["_music_effects_frame_sig"] = None
            item.payload = payload

    def _mgl_music_effect_curve(self, payload: Dict[str, Any], cache_path: str):
        if np is None or not cache_path:
            return None
        cached = payload.get("_music_effects_curve")
        if isinstance(cached, dict) and str(cached.get("path") or "") == str(cache_path):
            return cached
        try:
            with np.load(str(cache_path), allow_pickle=False) as data:
                times = np.asarray(data["times_s"], dtype="f4").reshape(-1)
                beat = np.asarray(data["beat_strength"], dtype="f4").reshape(-1)
        except Exception:
            return None
        if times.size == 0 or beat.size == 0 or times.size != beat.size:
            return None
        cached = {"path": str(cache_path), "times": times, "beat": beat}
        payload["_music_effects_curve"] = cached
        return cached

    def _mgl_refresh_music_effects_mesh_item(self, item: MGLSceneItem) -> None:
        if np is None or item is None:
            return
        payload = item.payload or {}
        if isinstance(payload.get("_fbx_skin_runtime"), dict):
            return
        cfg = self._mgl_normalize_music_effects_config(payload.get("music_effects"))
        if cfg is None or not bool(cfg.get("enabled", True)):
            self._mgl_restore_music_effects_mesh_item(item)
            return
        cache_path = str(cfg.get("cache_path") or "").strip()
        if not cache_path:
            self._mgl_restore_music_effects_mesh_item(item)
            return
        curve = self._mgl_music_effect_curve(payload, cache_path)
        if not isinstance(curve, dict):
            self._mgl_restore_music_effects_mesh_item(item)
            return

        frame = self._mgl_timeline_frame_index()
        sig = (
            frame,
            cache_path,
            round(float(cfg.get("gain", 1.0)), 6),
            round(float(cfg.get("threshold", 0.05)), 6),
            round(float(cfg.get("offset_s", 0.0)), 6),
            round(float(cfg.get("displacement", 0.15)), 6),
            round(float(cfg.get("max_displacement", 1.0)), 6),
            bool(cfg.get("outward_only", True)),
        )
        if payload.get("_music_effects_frame_sig") == sig:
            return

        time_s = float(self._mgl_timeline_time_seconds()) - float(cfg.get("offset_s", 0.0))
        times = np.asarray(curve.get("times"), dtype="f4").reshape(-1)
        beat = np.asarray(curve.get("beat"), dtype="f4").reshape(-1)
        if times.size == 0 or beat.size == 0:
            self._mgl_restore_music_effects_mesh_item(item)
            return
        if time_s < float(times[0]) or time_s > float(times[-1]):
            raw_strength = 0.0
        else:
            raw_strength = float(np.interp(time_s, times, beat))
        threshold = float(cfg.get("threshold", 0.05))
        if raw_strength <= threshold:
            strength = 0.0
        else:
            strength = (raw_strength - threshold) / max(1.0e-6, 1.0 - threshold)
        strength = max(0.0, min(1.0, strength * float(cfg.get("gain", 1.0))))
        amount = min(
            float(cfg.get("max_displacement", 1.0)),
            float(cfg.get("displacement", 0.15)) * strength,
        )
        if bool(cfg.get("outward_only", True)):
            amount = max(0.0, amount)

        copy_cfg = payload.get("copy_to_points")
        if isinstance(copy_cfg, dict) and bool(copy_cfg.get("gpu_instances", False)):
            self._mgl_restore_music_effects_mesh_item(item)
            payload = item.payload or {}
            self._mgl_apply_music_effects_to_gpu_instances(item, amount)
            payload = item.payload or {}
            payload["_music_effects_frame_sig"] = sig
            item.payload = payload
            return

        packed_copies = self._mgl_music_effect_packed_copies(payload)
        if packed_copies:
            entries = self._mgl_music_effect_entries(payload)
            entry = entries[0] if entries else None
            if isinstance(entry, dict):
                try:
                    points = np.asarray(entry.get("points"), dtype="f4").reshape(-1, 3)
                    normals = np.asarray(entry.get("normals"), dtype="f4").reshape(-1, 3)
                except Exception:
                    points = normals = None
                if points is not None and normals is not None and points.shape == normals.shape:
                    if entry.get("_music_effects_base_points") is None:
                        entry["_music_effects_base_points"] = points.copy()
                        entry["_music_effects_base_normals"] = normals.copy()
                    try:
                        base_points = np.asarray(entry.get("_music_effects_base_points"), dtype="f4").reshape(-1, 3)
                    except Exception:
                        base_points = points.copy()
                        entry["_music_effects_base_points"] = base_points.copy()
                    deformed = base_points.copy()
                    for copy in packed_copies:
                        try:
                            start = max(0, int(copy.get("vertex_start", 0) or 0))
                            count = max(0, int(copy.get("vertex_count", 0) or 0))
                            normal = np.asarray(copy.get("packed_normal"), dtype="f4").reshape(3)
                        except Exception:
                            continue
                        end = min(int(deformed.shape[0]), int(start + count))
                        if end <= start:
                            continue
                        normal_len = float(np.linalg.norm(normal))
                        if normal_len <= 1.0e-6:
                            continue
                        normal = normal / normal_len
                        deformed[start:end] = base_points[start:end] + (normal * float(amount))
                    try:
                        vbo = entry.get("vbo")
                        if vbo is not None:
                            vbo.write(deformed.astype("f4", copy=False).tobytes())
                        entry["points"] = deformed.astype("f4", copy=False)
                        entry["_music_effects_applied"] = True
                        payload["_music_effects_frame_sig"] = sig
                        item.payload = payload
                        return
                    except Exception:
                        pass

        for entry in self._mgl_music_effect_entries(payload):
            try:
                points = np.asarray(entry.get("points"), dtype="f4").reshape(-1, 3)
                normals = np.asarray(entry.get("normals"), dtype="f4").reshape(-1, 3)
            except Exception:
                continue
            if points.size == 0 or normals.shape != points.shape:
                continue
            if entry.get("_music_effects_base_points") is None:
                entry["_music_effects_base_points"] = points.copy()
                entry["_music_effects_base_normals"] = normals.copy()
            try:
                base_points = np.asarray(entry.get("_music_effects_base_points"), dtype="f4").reshape(-1, 3)
                base_normals = np.asarray(entry.get("_music_effects_base_normals"), dtype="f4").reshape(-1, 3)
            except Exception:
                continue
            if base_points.shape != points.shape or base_normals.shape != points.shape:
                entry["_music_effects_base_points"] = points.copy()
                entry["_music_effects_base_normals"] = normals.copy()
                base_points = points.copy()
                base_normals = normals.copy()
            deformed = (base_points + (base_normals * float(amount))).astype("f4", copy=False)
            try:
                vbo = entry.get("vbo")
                if vbo is not None:
                    vbo.write(deformed.tobytes())
                entry["points"] = deformed
                entry["_music_effects_applied"] = True
            except Exception:
                continue
        payload["_music_effects_frame_sig"] = sig
        item.payload = payload

    def _mgl_normalize_splat_physics_config(self, raw) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None

        def _bool(name: str, default: bool) -> bool:
            value = raw.get(name, default)
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "on", "y"}:
                    return True
                if text in {"0", "false", "no", "off", "n"}:
                    return False
            return bool(value)

        def _float(name: str, default: float, minimum: float, maximum: float) -> float:
            try:
                value = float(raw.get(name, default))
            except Exception:
                value = float(default)
            return max(float(minimum), min(float(maximum), float(value)))

        def _int(name: str, default: int, minimum: int, maximum: int) -> int:
            try:
                value = int(round(float(raw.get(name, default))))
            except Exception:
                value = int(default)
            return max(int(minimum), min(int(maximum), int(value)))

        gravity_raw = raw.get("gravity")
        if isinstance(gravity_raw, (list, tuple)) and len(gravity_raw) >= 3:
            try:
                gravity = (
                    max(-50.0, min(50.0, float(gravity_raw[0]))),
                    max(-50.0, min(50.0, float(gravity_raw[1]))),
                    max(-50.0, min(50.0, float(gravity_raw[2]))),
                )
            except Exception:
                gravity = (0.0, -0.25, 0.0)
        else:
            gravity = (
                _float("gravity_x", 0.0, -50.0, 50.0),
                _float("gravity_y", -0.25, -50.0, 50.0),
                _float("gravity_z", 0.0, -50.0, 50.0),
            )

        return {
            "enabled": _bool("enabled", True),
            "debug_log": _bool("debug_log", False),
            "follow_strength": _float("follow_strength", 12.0, 0.0, 100.0),
            "drag": _float("drag", 0.35, 0.0, 100.0),
            "velocity_scale": _float("velocity_scale", 0.15, 0.0, 4.0),
            "physics_scale": _float("physics_scale", 1.0, 0.01, 100.0),
            "noise_mode": str(raw.get("noise_mode") or "none").strip().lower(),
            "noise_strength": _float("noise_strength", 0.0, 0.0, 20.0),
            "noise_scale": _float("noise_scale", 1.5, 0.01, 100.0),
            "noise_speed": _float("noise_speed", 0.75, 0.0, 20.0),
            "gravity": gravity,
            "substeps": _int("substeps", 2, 1, 32),
            "max_lag": _float("max_lag", 2.5, 0.0, 1000.0),
            "reset_on_jump": _bool("reset_on_jump", True),
            "reset_frame_jump": _int("reset_frame_jump", 12, 1, 240),
            "trail_enabled": _bool("trail_enabled", False),
            "trail_spawn_rate": _float("trail_spawn_rate", 0.0, 0.0, 1.0),
            "trail_lifetime": _int("trail_lifetime", 24, 1, 240),
            "trail_alpha": _float("trail_alpha", 0.35, 0.0, 1.0),
            "trail_radius_scale": _float("trail_radius_scale", 0.75, 0.01, 4.0),
            "trail_curl": _float("trail_curl", 0.0, 0.0, 20.0),
            "trail_max_particles": _int("trail_max_particles", 120000, 0, 1000000),
            "glow_enabled": _bool("glow_enabled", False),
            "glow_intensity": _float("glow_intensity", 1.0, 0.0, 8.0),
            "glow_radius_boost": _float("glow_radius_boost", 0.25, 0.0, 4.0),
        }

    def _mgl_splat_physics_signature(self, config: dict | None):
        if not isinstance(config, dict):
            return None
        def _num(name: str, default: float) -> float:
            try:
                return float(config.get(name, default))
            except Exception:
                return float(default)

        gravity = config.get("gravity") or (0.0, 0.0, 0.0)
        try:
            gravity_sig = tuple(round(float(v), 5) for v in list(gravity)[:3])
        except Exception:
            gravity_sig = (0.0, 0.0, 0.0)
        return (
            bool(config.get("enabled", True)),
            round(_num("follow_strength", 12.0), 5),
            round(_num("drag", 0.35), 5),
            round(_num("velocity_scale", 0.15), 5),
            round(_num("physics_scale", 1.0), 5),
            str(config.get("noise_mode") or "none"),
            round(_num("noise_strength", 0.0), 5),
            round(_num("noise_scale", 1.5), 5),
            round(_num("noise_speed", 0.75), 5),
            gravity_sig,
            int(config.get("substeps", 2) or 2),
            round(_num("max_lag", 2.5), 5),
            bool(config.get("reset_on_jump", True)),
            int(config.get("reset_frame_jump", 12) or 12),
            bool(config.get("trail_enabled", False)),
            round(_num("trail_spawn_rate", 0.0), 5),
            int(config.get("trail_lifetime", 24) or 24),
            round(_num("trail_alpha", 0.35), 5),
            round(_num("trail_radius_scale", 0.75), 5),
            round(_num("trail_curl", 0.0), 5),
            int(config.get("trail_max_particles", 120000) or 120000),
            bool(config.get("glow_enabled", False)),
            round(_num("glow_intensity", 1.0), 5),
            round(_num("glow_radius_boost", 0.25), 5),
        )

    def _mgl_splat_noise_vectors(self, positions: NDArray, frame: int, config: dict) -> Tuple[NDArray, NDArray]:
        pts = np.asarray(positions, dtype="f4").reshape(-1, 3)
        if pts.size == 0:
            return pts.astype("f4", copy=True), np.zeros((0,), dtype="f4")
        try:
            noise_scale = max(0.01, float(config.get("noise_scale", 1.5)))
        except Exception:
            noise_scale = 1.5
        try:
            physics_scale = max(0.01, float(config.get("physics_scale", 1.0)))
        except Exception:
            physics_scale = 1.0
        noise_scale = max(0.01, noise_scale / physics_scale)
        try:
            noise_speed = max(0.0, float(config.get("noise_speed", 0.75)))
        except Exception:
            noise_speed = 0.75
        phase = (float(frame) / max(1.0, float(self._mgl_timeline_fps_value()))) * float(noise_speed)
        p = pts * np.float32(noise_scale)
        x = p[:, 0]
        y = p[:, 1]
        z = p[:, 2]

        curl_x = (-1.33 * np.sin(1.33 * y + 0.80 * phase)) - (1.41 * np.cos(1.41 * z + 1.30 * phase))
        curl_y = (-1.11 * np.sin(1.11 * z - 0.70 * phase)) - (1.57 * np.cos(1.57 * x - 0.90 * phase))
        curl_z = (-1.27 * np.sin(1.27 * x + 0.60 * phase)) - (1.37 * np.cos(1.37 * y + phase))
        vectors = np.stack([curl_x, curl_y, curl_z], axis=1).astype("f4", copy=False)
        length = np.linalg.norm(vectors, axis=1)
        vectors = vectors / np.maximum(length[:, None], np.float32(1.0e-6))
        scalar = (
            vectors[:, 0] * np.float32(0.57)
            + vectors[:, 1] * np.float32(0.31)
            + vectors[:, 2] * np.float32(0.12)
        ).astype("f4", copy=False)
        scalar = np.clip(scalar, np.float32(-1.0), np.float32(1.0))
        return vectors.astype("f4", copy=False), scalar

    @staticmethod
    def _mgl_clear_splat_trail_state(proxy: dict) -> None:
        if not isinstance(proxy, dict):
            return
        for key in (
            "trail_splats",
            "trail_velocities",
            "trail_ages",
            "trail_last_frame",
            "trail_emit_accum",
            "trail_emit_counter",
        ):
            try:
                proxy.pop(key, None)
            except Exception:
                pass

    def _mgl_update_splat_trails(self, proxy: dict, source_splats: NDArray, frame: int) -> NDArray:
        if np is None or not isinstance(proxy, dict):
            return np.zeros((0, 15), dtype=np.float32) if np is not None else source_splats
        if not self._mgl_timeline_fx_enabled():
            self._mgl_clear_splat_trail_state(proxy)
            return np.zeros((0, 15), dtype=np.float32)
        config = proxy.get("splat_physics")
        if not isinstance(config, dict):
            self._mgl_clear_splat_trail_state(proxy)
            return np.zeros((0, 15), dtype=np.float32)

        def _empty() -> NDArray:
            return np.zeros((0, 15), dtype=np.float32)

        try:
            source = np.asarray(source_splats, dtype="f4").reshape(-1, 15)
        except Exception:
            self._mgl_clear_splat_trail_state(proxy)
            return _empty()
        count = int(source.shape[0])
        if count <= 0:
            self._mgl_clear_splat_trail_state(proxy)
            return _empty()

        try:
            spawn_rate = max(0.0, min(1.0, float(config.get("trail_spawn_rate", 0.0))))
        except Exception:
            spawn_rate = 0.0
        try:
            lifetime = max(1, int(config.get("trail_lifetime", 24) or 24))
        except Exception:
            lifetime = 24
        try:
            trail_alpha = max(0.0, min(1.0, float(config.get("trail_alpha", 0.35))))
        except Exception:
            trail_alpha = 0.35
        try:
            max_particles = max(0, int(config.get("trail_max_particles", 120000) or 120000))
        except Exception:
            max_particles = 120000
        enabled = bool(config.get("trail_enabled", False)) and spawn_rate > 0.0 and lifetime > 0 and trail_alpha > 0.0
        if not enabled or max_particles <= 0:
            self._mgl_clear_splat_trail_state(proxy)
            return _empty()

        try:
            frame_delta = int(frame) - int(proxy.get("trail_last_frame"))
        except Exception:
            frame_delta = 1
        if frame_delta == 0:
            frame_delta = 1
        reset = bool(proxy.get("physics_reset", False))
        if bool(config.get("reset_on_jump", True)):
            try:
                jump = max(1, int(config.get("reset_frame_jump", 12) or 12))
            except Exception:
                jump = 12
            if frame_delta < 0 or abs(int(frame_delta)) > jump:
                reset = True
        if reset:
            self._mgl_clear_splat_trail_state(proxy)
            frame_delta = 1

        try:
            fps = max(1.0, float(self._mgl_timeline_fps_value()))
        except Exception:
            fps = 24.0
        dt = max(1.0 / fps, min(float(abs(frame_delta)) / fps, 0.25))
        age_step = max(1.0, float(abs(frame_delta)))

        trail_splats = proxy.get("trail_splats")
        trail_velocities = proxy.get("trail_velocities")
        trail_ages = proxy.get("trail_ages")
        try:
            trail_splats = np.asarray(trail_splats, dtype="f4").reshape(-1, 15)
            trail_velocities = np.asarray(trail_velocities, dtype="f4").reshape(-1, 3)
            trail_ages = np.asarray(trail_ages, dtype="f4").reshape(-1)
            if (
                int(trail_splats.shape[0]) != int(trail_velocities.shape[0])
                or int(trail_splats.shape[0]) != int(trail_ages.shape[0])
            ):
                raise ValueError("trail state shape mismatch")
        except Exception:
            trail_splats = _empty()
            trail_velocities = np.zeros((0, 3), dtype=np.float32)
            trail_ages = np.zeros((0,), dtype=np.float32)

        if int(trail_splats.shape[0]) > 0:
            trail_ages = trail_ages + np.float32(age_step)
            keep = trail_ages < np.float32(float(lifetime))
            if np.any(keep):
                trail_splats = trail_splats[keep].astype(np.float32, copy=True)
                trail_velocities = trail_velocities[keep].astype(np.float32, copy=True)
                trail_ages = trail_ages[keep].astype(np.float32, copy=True)
                try:
                    physics_scale = max(0.01, float(config.get("physics_scale", 1.0)))
                except Exception:
                    physics_scale = 1.0
                try:
                    drag = max(0.0, min(100.0, float(config.get("drag", 0.35))))
                except Exception:
                    drag = 0.35
                try:
                    gravity = np.asarray(config.get("gravity", (0.0, -0.25, 0.0)), dtype="f4").reshape(-1)[:3]
                    if gravity.shape[0] != 3:
                        gravity = np.array([0.0, -0.25, 0.0], dtype="f4")
                except Exception:
                    gravity = np.array([0.0, -0.25, 0.0], dtype="f4")
                gravity = gravity * np.float32(physics_scale)
                acceleration = np.repeat(gravity[None, :], int(trail_splats.shape[0]), axis=0).astype("f4", copy=False)
                try:
                    trail_curl = max(0.0, float(config.get("trail_curl", 0.0)))
                except Exception:
                    trail_curl = 0.0
                if trail_curl > 1.0e-6:
                    noise_vectors, _noise_scalar = self._mgl_splat_noise_vectors(trail_splats[:, 0:3], int(frame), config)
                    acceleration += noise_vectors * np.float32(trail_curl * physics_scale)
                damping = math.exp(-float(drag) * 8.0 * float(dt))
                trail_velocities = (trail_velocities + acceleration * np.float32(dt)) * np.float32(damping)
                trail_splats[:, 0:3] = trail_splats[:, 0:3] + trail_velocities * np.float32(dt)
            else:
                trail_splats = _empty()
                trail_velocities = np.zeros((0, 3), dtype=np.float32)
                trail_ages = np.zeros((0,), dtype=np.float32)

        existing_count = int(trail_splats.shape[0])
        capacity = max(0, int(max_particles) - existing_count)
        emit_accum = 0.0 if capacity <= 0 else float(proxy.get("trail_emit_accum", 0.0) or 0.0)
        emit_accum += float(count) * float(spawn_rate)
        spawn_count = int(math.floor(emit_accum))
        if spawn_count > 0:
            emit_accum -= float(spawn_count)
        spawn_count = min(int(spawn_count), int(count), int(capacity))
        if capacity <= 0:
            emit_accum = 0.0
        elif spawn_count >= capacity:
            emit_accum = 0.0

        if spawn_count > 0:
            try:
                emit_counter = int(proxy.get("trail_emit_counter", 0) or 0)
            except Exception:
                emit_counter = 0
            candidate_count = min(int(count), max(int(spawn_count), int(spawn_count) * 4))
            stride = max(1, int(count) // max(1, int(candidate_count)))
            offset = int((int(frame) * 7919 + emit_counter * 104729) % max(1, int(count)))
            candidate_idx = (offset + np.arange(candidate_count, dtype=np.int64) * int(stride)) % int(count)

            choose_idx = candidate_idx[:spawn_count]
            try:
                noise_strength = max(0.0, float(config.get("noise_strength", 0.0)))
            except Exception:
                noise_strength = 0.0
            try:
                trail_curl_for_mask = max(0.0, float(config.get("trail_curl", 0.0)))
            except Exception:
                trail_curl_for_mask = 0.0
            if int(candidate_idx.shape[0]) > int(spawn_count) and (noise_strength > 1.0e-6 or trail_curl_for_mask > 1.0e-6):
                _vectors, scalar = self._mgl_splat_noise_vectors(source[candidate_idx, 0:3], int(frame), config)
                jitter_raw = (
                    (candidate_idx.astype(np.uint64) * np.uint64(1103515245) + np.uint64(int(frame) * 12345))
                    & np.uint64(0xFFFF)
                ).astype(np.float32)
                jitter = (jitter_raw / np.float32(65535.0)) * np.float32(0.25)
                scores = scalar.astype(np.float32, copy=False) + jitter
                pick = np.argpartition(scores, -int(spawn_count))[-int(spawn_count):]
                choose_idx = candidate_idx[pick]

            emitted = source[choose_idx].astype(np.float32, copy=True)
            emitted[:, 6] *= np.float32(trail_alpha)
            try:
                radius_scale = max(0.01, min(4.0, float(config.get("trail_radius_scale", 0.75))))
            except Exception:
                radius_scale = 0.75
            emitted[:, 7] *= np.float32(radius_scale)

            spawned_velocities = np.zeros((int(emitted.shape[0]), 3), dtype=np.float32)
            target_velocity = proxy.get("physics_target_velocity")
            try:
                target_velocity = np.asarray(target_velocity, dtype="f4").reshape(-1, 3)
                if int(target_velocity.shape[0]) == int(count):
                    try:
                        velocity_scale = max(0.0, float(config.get("velocity_scale", 0.15)))
                    except Exception:
                        velocity_scale = 0.15
                    inherit = max(0.0, min(1.0, float(velocity_scale) * 0.25))
                    if inherit > 1.0e-6:
                        spawned_velocities += target_velocity[choose_idx].astype(np.float32, copy=False) * np.float32(inherit)
            except Exception:
                pass
            try:
                trail_curl = max(0.0, float(config.get("trail_curl", 0.0)))
                physics_scale = max(0.01, float(config.get("physics_scale", 1.0)))
            except Exception:
                trail_curl = 0.0
                physics_scale = 1.0
            if trail_curl > 1.0e-6:
                noise_vectors, _noise_scalar = self._mgl_splat_noise_vectors(emitted[:, 0:3], int(frame), config)
                spawned_velocities += noise_vectors * np.float32(trail_curl * physics_scale * 0.05)

            spawned_ages = np.zeros((int(emitted.shape[0]),), dtype=np.float32)
            if existing_count > 0:
                trail_splats = np.concatenate([trail_splats, emitted], axis=0)
                trail_velocities = np.concatenate([trail_velocities, spawned_velocities], axis=0)
                trail_ages = np.concatenate([trail_ages, spawned_ages], axis=0)
            else:
                trail_splats = emitted
                trail_velocities = spawned_velocities
                trail_ages = spawned_ages
            proxy["trail_emit_counter"] = emit_counter + 1

        proxy["trail_emit_accum"] = float(emit_accum)
        proxy["trail_splats"] = trail_splats.astype(np.float32, copy=False)
        proxy["trail_velocities"] = trail_velocities.astype(np.float32, copy=False)
        proxy["trail_ages"] = trail_ages.astype(np.float32, copy=False)
        proxy["trail_last_frame"] = int(frame)

        if int(trail_splats.shape[0]) <= 0:
            return _empty()
        fade = np.clip(
            np.float32(1.0) - (trail_ages.astype(np.float32, copy=False) / np.float32(max(1.0, float(lifetime)))),
            np.float32(0.0),
            np.float32(1.0),
        )
        fade = fade * fade
        out = trail_splats.astype(np.float32, copy=True)
        out[:, 6] *= fade
        out[:, 7] *= np.maximum(np.float32(0.15), fade)
        return out.astype(np.float32, copy=False)

    def _mgl_apply_splat_physics(self, proxy: dict, target_positions: NDArray, frame: int) -> NDArray:
        if np is None or not isinstance(proxy, dict):
            return target_positions
        config = proxy.get("splat_physics")
        if not self._mgl_timeline_fx_enabled() or not isinstance(config, dict) or not bool(config.get("enabled", True)):
            proxy.pop("physics_positions", None)
            proxy.pop("physics_velocities", None)
            proxy.pop("physics_last_target", None)
            proxy.pop("physics_last_frame", None)
            proxy.pop("physics_target_velocity", None)
            proxy.pop("physics_dt", None)
            proxy.pop("physics_frame_delta", None)
            proxy.pop("physics_glow_values", None)
            proxy["physics_reset"] = True
            self._mgl_clear_splat_trail_state(proxy)
            proxy["physics_config_signature"] = self._mgl_splat_physics_signature(config)
            return target_positions

        target = np.asarray(target_positions, dtype="f4").reshape(-1, 3)
        count = int(target.shape[0])
        sig = self._mgl_splat_physics_signature(config)
        positions = proxy.get("physics_positions")
        velocities = proxy.get("physics_velocities")
        last_target = proxy.get("physics_last_target")
        last_frame = proxy.get("physics_last_frame")
        config_changed = proxy.get("physics_config_signature") != sig
        reset = bool(config_changed)
        if positions is None or velocities is None or last_target is None:
            reset = True
        else:
            try:
                reset = reset or int(np.asarray(positions).shape[0]) != count
                reset = reset or int(np.asarray(velocities).shape[0]) != count
                reset = reset or int(np.asarray(last_target).shape[0]) != count
            except Exception:
                reset = True
        if last_frame is None:
            reset = True
            frame_delta = 1
        else:
            try:
                frame_delta = int(frame) - int(last_frame)
            except Exception:
                frame_delta = 1
        if bool(config.get("reset_on_jump", True)):
            jump = max(1, int(config.get("reset_frame_jump", 12) or 12))
            if frame_delta < 0 or abs(int(frame_delta)) > jump:
                reset = True

        if reset:
            positions = target.astype("f4", copy=True)
            velocities = np.zeros_like(positions, dtype="f4")
            last_target = target.astype("f4", copy=True)
            frame_delta = 1
        else:
            positions = np.asarray(positions, dtype="f4").reshape(-1, 3).copy()
            velocities = np.asarray(velocities, dtype="f4").reshape(-1, 3).copy()
            last_target = np.asarray(last_target, dtype="f4").reshape(-1, 3)
            if frame_delta == 0:
                frame_delta = 1

        fps = max(1.0, float(self._mgl_timeline_fps_value()))
        dt = max(1.0 / fps, min(float(abs(frame_delta)) / fps, 0.25))
        substeps = max(1, int(config.get("substeps", 2) or 2))
        step_dt = float(dt) / float(substeps)
        try:
            follow = max(0.0, float(config.get("follow_strength", 12.0)))
        except Exception:
            follow = 12.0
        try:
            drag = max(0.0, min(100.0, float(config.get("drag", 0.35))))
        except Exception:
            drag = 0.35
        try:
            velocity_scale = max(0.0, float(config.get("velocity_scale", 0.15)))
        except Exception:
            velocity_scale = 0.15
        try:
            physics_scale = max(0.01, float(config.get("physics_scale", 1.0)))
        except Exception:
            physics_scale = 1.0
        noise_mode = str(config.get("noise_mode") or "none").strip().lower()
        if noise_mode not in {"none", "curl_force", "velocity_multiply", "follow_multiply", "drag_multiply"}:
            noise_mode = "none"
        try:
            noise_strength = max(0.0, float(config.get("noise_strength", 0.0)))
        except Exception:
            noise_strength = 0.0
        try:
            max_lag = max(0.0, float(config.get("max_lag", 2.5)))
        except Exception:
            max_lag = 2.5
        max_lag = max_lag * physics_scale
        try:
            gravity = np.asarray(config.get("gravity", (0.0, -0.25, 0.0)), dtype="f4").reshape(-1)[:3]
            if gravity.shape[0] != 3:
                gravity = np.array([0.0, -0.25, 0.0], dtype="f4")
        except Exception:
            gravity = np.array([0.0, -0.25, 0.0], dtype="f4")
        gravity = gravity * np.float32(physics_scale)

        target_velocity = (target - last_target) / max(float(dt), 1.0e-6)
        proxy["physics_target_velocity"] = target_velocity.astype("f4", copy=False)
        proxy["physics_dt"] = float(dt)
        proxy["physics_frame_delta"] = int(frame_delta)
        proxy["physics_reset"] = bool(reset)
        noise_vectors = None
        noise_scalar = None
        if noise_mode != "none" and noise_strength > 1.0e-6:
            noise_vectors, noise_scalar = self._mgl_splat_noise_vectors(target, int(frame), config)
        if velocity_scale > 0.0:
            if noise_mode == "velocity_multiply" and noise_scalar is not None:
                velocity_mult = np.clip(
                    np.float32(1.0) + (np.float32(noise_strength) * noise_scalar),
                    np.float32(0.0),
                    np.float32(4.0),
                )
                velocities += target_velocity * np.float32(velocity_scale) * velocity_mult[:, None]
            else:
                velocities += target_velocity * np.float32(velocity_scale)

        damping = math.exp(-float(drag) * 8.0 * float(step_dt))
        damping_values = None
        if noise_mode == "drag_multiply" and noise_scalar is not None:
            drag_mult = np.clip(
                np.float32(1.0) + (np.float32(noise_strength) * noise_scalar),
                np.float32(0.05),
                np.float32(4.0),
            )
            damping_values = np.exp(-float(drag) * drag_mult * np.float32(8.0 * step_dt)).astype("f4", copy=False)
        follow_values = None
        if noise_mode == "follow_multiply" and noise_scalar is not None:
            follow_mult = np.clip(
                np.float32(1.0) + (np.float32(noise_strength) * noise_scalar),
                np.float32(0.0),
                np.float32(4.0),
            )
            follow_values = np.float32(follow) * follow_mult
        for _idx in range(substeps):
            if follow_values is not None:
                acceleration = (target - positions) * follow_values[:, None]
            else:
                acceleration = (target - positions) * np.float32(follow)
            acceleration += gravity[None, :]
            if noise_mode == "curl_force" and noise_vectors is not None:
                acceleration += noise_vectors * np.float32(noise_strength * physics_scale)
            if damping_values is not None:
                velocities = (velocities + acceleration * np.float32(step_dt)) * damping_values[:, None]
            else:
                velocities = (velocities + acceleration * np.float32(step_dt)) * np.float32(damping)
            positions = positions + velocities * np.float32(step_dt)
            if max_lag > 0.0:
                offset = positions - target
                dist = np.linalg.norm(offset, axis=1)
                mask = dist > max_lag
                if np.any(mask):
                    safe_dist = np.maximum(dist[mask], np.float32(1.0e-6))
                    positions[mask] = target[mask] + offset[mask] * (np.float32(max_lag) / safe_dist)[:, None]
                    vel_dot = np.sum(velocities[mask] * offset[mask], axis=1)
                    away = vel_dot > 0.0
                    if np.any(away):
                        masked_indices = np.nonzero(mask)[0][away]
                        velocities[masked_indices] *= np.float32(0.25)

        proxy["physics_positions"] = positions.astype("f4", copy=False)
        proxy["physics_velocities"] = velocities.astype("f4", copy=False)
        proxy["physics_last_target"] = target.astype("f4", copy=True)
        proxy["physics_last_frame"] = int(frame)
        proxy["physics_config_signature"] = sig
        try:
            offset_mag = np.linalg.norm(positions - target, axis=1).astype("f4", copy=False)
            velocity_mag = np.linalg.norm(velocities, axis=1).astype("f4", copy=False)
            lag_scale = max(1.0e-6, float(max_lag) * 0.65) if max_lag > 0.0 else max(1.0e-6, float(physics_scale))
            vel_scale = max(1.0e-6, float(physics_scale) * 4.0)
            lag_metric = np.clip(offset_mag / np.float32(lag_scale), np.float32(0.0), np.float32(1.0))
            vel_metric = np.clip(velocity_mag / np.float32(vel_scale), np.float32(0.0), np.float32(1.0))
            proxy["physics_glow_values"] = np.maximum(lag_metric, vel_metric * np.float32(0.45)).astype("f4", copy=False)
        except Exception:
            proxy.pop("physics_glow_values", None)
        if bool(config.get("debug_log", False)):
            try:
                max_offset = float(np.linalg.norm(positions - target, axis=1).max()) if count else 0.0
                self._mgl_log_throttled(
                    "_mgl_splat_physics_" + str(proxy.get("owner") or ""),
                    "fx_splat_physics: owner="
                    + str(proxy.get("owner") or "")
                    + " frame="
                    + str(int(frame))
                    + " count="
                    + str(count)
                    + " max_offset="
                    + f"{max_offset:.4f}",
                    0.5,
                )
            except Exception:
                pass
        return positions.astype("f4", copy=False)

    def _mgl_load_skinned_splat_proxy(
        self,
        owner: str,
        render_proxy: dict,
        fbx_rig_context: dict | None,
    ) -> Optional[Tuple[NDArray, NDArray]]:
        if np is None or not isinstance(render_proxy, dict):
            return None
        proxy_type = str(render_proxy.get("type") or "").strip().lower()
        if proxy_type not in {"skinned_splat", "skinned_gaussian_splat"}:
            return None

        owner_key = str(owner or "").strip()
        if not owner_key:
            return None

        manifest_data = {}
        manifest_path = None
        manifest_raw = str(render_proxy.get("manifest") or "").strip()
        if manifest_raw:
            try:
                manifest_path = Path(manifest_raw)
                if manifest_path.exists():
                    with manifest_path.open("r", encoding="utf-8") as handle:
                        loaded = json.load(handle)
                    if isinstance(loaded, dict):
                        manifest_data = loaded
            except Exception as exc:
                try:
                    self._mgl_log("skinned_splat_proxy: manifest read failed owner=" + owner_key + " err=" + repr(exc))
                except Exception:
                    pass
                manifest_data = {}

        manifest_dir = manifest_path.parent if manifest_path is not None else None

        def _resolve_path(*keys: str) -> Optional[Path]:
            raw = ""
            for key in keys:
                raw = str(render_proxy.get(key) or "").strip()
                if raw:
                    break
                raw = str(manifest_data.get(key) or "").strip()
                if raw:
                    break
            if not raw:
                return None
            try:
                path = Path(raw)
                if not path.is_absolute() and manifest_dir is not None:
                    path = manifest_dir / path
                return path
            except Exception:
                return None

        ply_path = _resolve_path("splat_ply", "ply")
        skin_path = _resolve_path("skin_npz", "proxy_skin", "skin")
        if ply_path is None or skin_path is None:
            return None
        if not ply_path.exists() or not skin_path.exists():
            try:
                self._mgl_log(
                    "skinned_splat_proxy: files missing owner="
                    + owner_key
                    + " ply="
                    + str(ply_path)
                    + " skin="
                    + str(skin_path)
                )
            except Exception:
                pass
            return None

        try:
            from echograph.util.splats_io import load_splats_ply

            try:
                sample_limit = int(render_proxy.get("sample_count") or manifest_data.get("sample_count") or 200_000)
            except Exception:
                sample_limit = 200_000
            sample_limit = max(1, int(sample_limit))
            splats = np.asarray(load_splats_ply(str(ply_path), n=sample_limit), dtype=np.float32)
            if splats.ndim != 2 or int(splats.shape[1]) != 15 or int(splats.shape[0]) <= 0:
                raise RuntimeError(f"expected skinned proxy splat array shape (N,15), got {splats.shape}")

            with np.load(str(skin_path), allow_pickle=False) as skin:
                bind_positions = np.asarray(skin["bind_positions"], dtype=np.float32).reshape(-1, 3)
                joint_indices = np.asarray(skin["joint_indices"], dtype=np.int64)
                joint_weights = np.asarray(skin["joint_weights"], dtype=np.float32)
                try:
                    bind_quats = np.asarray(skin["bind_quats"], dtype=np.float32).reshape(-1, 4)
                except Exception:
                    bind_quats = splats[:, 11:15].astype(np.float32, copy=True)
                try:
                    bind_radius_scale = np.asarray(skin["bind_radius_scale"], dtype=np.float32).reshape(-1, 4)
                except Exception:
                    bind_radius_scale = splats[:, 7:11].astype(np.float32, copy=True)
                try:
                    source_triangle_area = np.asarray(skin["source_triangle_area"], dtype=np.float32).reshape(-1)
                except Exception:
                    source_triangle_area = np.zeros((0,), dtype=np.float32)
                try:
                    source_feature_radius = np.asarray(skin["source_feature_radius"], dtype=np.float32).reshape(-1)
                except Exception:
                    source_feature_radius = np.zeros((0,), dtype=np.float32)

            count = min(
                int(splats.shape[0]),
                int(bind_positions.shape[0]),
                int(joint_indices.shape[0]) if joint_indices.ndim == 2 else 0,
                int(joint_weights.shape[0]) if joint_weights.ndim == 2 else 0,
            )
            if count <= 0:
                raise RuntimeError("skinned proxy contains no usable splats")
            if count != int(splats.shape[0]):
                splats = splats[:count].astype(np.float32, copy=False)
            bind_positions = bind_positions[:count].astype(np.float32, copy=False)
            joint_indices = joint_indices[:count].astype(np.int64, copy=False)
            joint_weights = joint_weights[:count].astype(np.float32, copy=False)
            bind_quats = bind_quats[:count].astype(np.float32, copy=False)
            bind_radius_scale = bind_radius_scale[:count].astype(np.float32, copy=False)
            if int(source_triangle_area.shape[0]) >= count:
                source_triangle_area = source_triangle_area[:count].astype(np.float32, copy=False)
            else:
                source_triangle_area = np.zeros((0,), dtype=np.float32)
            if int(source_feature_radius.shape[0]) >= count:
                source_feature_radius = source_feature_radius[:count].astype(np.float32, copy=False)
            else:
                source_feature_radius = np.zeros((0,), dtype=np.float32)
            if joint_indices.ndim != 2 or joint_weights.ndim != 2 or joint_indices.shape != joint_weights.shape:
                raise RuntimeError("skinned proxy joint index/weight arrays must be matching 2D arrays")

            current = splats.astype(np.float32, copy=True)
            current[:, 0:3] = bind_positions
            current[:, 7:11] = bind_radius_scale
            current[:, 11:15] = bind_quats
            bind_mins = bind_positions.min(axis=0).astype("f4")
            bind_maxs = bind_positions.max(axis=0).astype("f4")
            physics_config = self._mgl_normalize_splat_physics_config(
                render_proxy.get("splat_physics") or manifest_data.get("splat_physics")
            )

            proxies = getattr(self, "_mgl_scene_skinned_splat_proxies_by_owner", None)
            if not isinstance(proxies, dict):
                proxies = {}
                self._mgl_scene_skinned_splat_proxies_by_owner = proxies
            proxies[owner_key] = {
                "owner": owner_key,
                "splat_ply": str(ply_path),
                "skin_npz": str(skin_path),
                "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                "base_splats": current.astype(np.float32, copy=True),
                "current_splats": current,
                "bind_positions": bind_positions,
                "bind_quats": bind_quats,
                "bind_radius_scale": bind_radius_scale,
                "source_triangle_area": source_triangle_area,
                "source_feature_radius": source_feature_radius,
                "bind_bounds": (bind_mins, bind_maxs),
                "joint_indices": joint_indices,
                "joint_weights": joint_weights,
                "render_proxy": dict(render_proxy),
                "splat_physics": physics_config,
                "physics_config_signature": self._mgl_splat_physics_signature(physics_config),
                "last_signature": None,
            }
            try:
                current, glow_values = self._mgl_apply_splat_fx_to_splats(
                    proxies[owner_key],
                    current,
                    int(self._mgl_timeline_frame_index()),
                )
                proxies[owner_key]["current_splats"] = current
                glow_map = getattr(self, "_mgl_scene_splat_glow_by_owner", None)
                if not isinstance(glow_map, dict):
                    glow_map = {}
                    self._mgl_scene_splat_glow_by_owner = glow_map
                glow_map[owner_key] = glow_values
            except Exception:
                pass

            try:
                self._mgl_scene_splats[owner_key] = current
            except Exception:
                pass
            try:
                self._mgl_scene_splats_bounds_local[owner_key] = (bind_mins, bind_maxs)
                self._mgl_scene_splat_bounds_by_owner[owner_key] = (bind_mins, bind_maxs)
                self._mgl_scene_bounds_by_owner[owner_key] = (bind_mins, bind_maxs)
            except Exception:
                pass
            try:
                self._mgl_log(
                    "skinned_splat_proxy: loaded owner="
                    + owner_key
                    + " count="
                    + str(int(current.shape[0]))
                    + " ply="
                    + str(ply_path)
                )
            except Exception:
                pass
            self._mgl_render_splats = True
            self._mgl_splats_need_rebuild = True
            return bind_mins, bind_maxs
        except Exception as exc:
            try:
                self._mgl_log("skinned_splat_proxy: load failed owner=" + owner_key + " err=" + repr(exc))
            except Exception:
                pass
            return None

    @staticmethod
    def _mgl_skin_splat_positions_row_major(
        skin_mats: NDArray,
        bind_positions: NDArray,
        joint_indices: NDArray,
        joint_weights: NDArray,
    ) -> NDArray:
        if np is None:
            raise RuntimeError("numpy unavailable")
        mats = np.asarray(skin_mats, dtype="f4").reshape(-1, 4, 4)
        bind = np.asarray(bind_positions, dtype="f4").reshape(-1, 3)
        ji = np.asarray(joint_indices, dtype=np.int64)
        jw = np.asarray(joint_weights, dtype="f4")
        if ji.ndim != 2 or jw.ndim != 2 or ji.shape != jw.shape or ji.shape[0] != bind.shape[0]:
            return bind.astype("f4", copy=True)
        joint_count = int(mats.shape[0])
        count = int(bind.shape[0])
        out = np.zeros((count, 3), dtype="f4")
        weight_sum = np.zeros((count,), dtype="f4")
        for slot in range(int(ji.shape[1])):
            js = ji[:, slot]
            ws = jw[:, slot]
            valid = (js >= 0) & (js < joint_count) & (ws > 1.0e-8)
            if not np.any(valid):
                continue
            transformed = MGLRendererMixin._mgl_fbx_transform_points_row_major(mats[js[valid]], bind[valid])
            out[valid] += transformed * ws[valid, None]
            weight_sum[valid] += ws[valid]
        weighted = weight_sum > 1.0e-8
        if np.any(weighted):
            out[weighted] = out[weighted] / np.maximum(weight_sum[weighted, None], np.float32(1.0e-8))
        if np.any(~weighted):
            out[~weighted] = bind[~weighted]
        return out.astype("f4", copy=False)

    def _mgl_update_skinned_splat_proxies(self, *, force: bool = False) -> bool:
        if np is None:
            return False
        proxies = getattr(self, "_mgl_scene_skinned_splat_proxies_by_owner", None)
        if not isinstance(proxies, dict) or not proxies:
            return False

        frame = self._mgl_timeline_frame_index()
        visibility = getattr(self, "_mgl_scene_visibility", {}) or {}
        timeline_fx_enabled = bool(self._mgl_timeline_fx_enabled())
        changed = False
        for owner, proxy in list(proxies.items()):
            owner_key = str(owner or "").strip()
            if not owner_key:
                continue
            if isinstance(visibility, dict) and not bool(visibility.get(owner_key, True)):
                continue
            context = proxy.get("fbx_rig_context") if isinstance(proxy, dict) else None
            if not isinstance(context, dict):
                continue
            skeleton = context.get("skeleton")
            if skeleton is None:
                continue
            clip = context.get("clip")
            loop = bool(context.get("loop", True))
            sample_seconds = self._mgl_fbx_context_timeline_sample_seconds(context, owner_key)
            signature = (
                int(frame),
                round(float(sample_seconds), 6),
                _safe_clip_signature(clip),
                id(skeleton),
                bool(timeline_fx_enabled),
            )
            if not force and proxy.get("last_signature") == signature:
                continue
            try:
                evaluation = evaluate_rig_at_time(
                    skeleton,
                    clip,
                    float(sample_seconds),
                    loop=loop,
                    include_debug_data=False,
                )
                skin_mats = np.asarray(evaluation.skin_matrices, dtype="f4").reshape(-1, 4, 4)
                bind_positions = np.asarray(proxy.get("bind_positions"), dtype="f4").reshape(-1, 3)
                joint_indices = np.asarray(proxy.get("joint_indices"), dtype=np.int64)
                joint_weights = np.asarray(proxy.get("joint_weights"), dtype="f4")
                deformed = self._mgl_skin_splat_positions_row_major(
                    skin_mats,
                    bind_positions,
                    joint_indices,
                    joint_weights,
                )
                physics_config = proxy.get("splat_physics") if isinstance(proxy, dict) else None
                if isinstance(physics_config, dict):
                    deformed = self._mgl_apply_splat_physics(proxy, deformed, int(frame))
                base = np.asarray(proxy.get("base_splats"), dtype="f4")
                if base.ndim != 2 or int(base.shape[1]) != 15 or int(base.shape[0]) != int(deformed.shape[0]):
                    continue
                current = base.astype(np.float32, copy=True)
                current[:, 0:3] = deformed
                try:
                    current = self._mgl_apply_splat_colorize_to_splats(proxy, current)
                except Exception:
                    pass
                try:
                    trail_splats = self._mgl_update_splat_trails(proxy, current, int(frame))
                    if getattr(trail_splats, "size", 0):
                        current = np.concatenate([current, trail_splats], axis=0).astype(np.float32, copy=False)
                except Exception as exc:
                    try:
                        self._mgl_log_throttled(
                            "_mgl_splat_trail_update_error_" + owner_key,
                            "fx_splat_physics: trail update failed owner=" + owner_key + " err=" + repr(exc),
                            1.0,
                        )
                    except Exception:
                        pass
                try:
                    current, glow_values = self._mgl_apply_splat_fx_to_splats(
                        proxy,
                        current,
                        int(frame),
                        apply_colorize=False,
                    )
                    glow_map = getattr(self, "_mgl_scene_splat_glow_by_owner", None)
                    if not isinstance(glow_map, dict):
                        glow_map = {}
                        self._mgl_scene_splat_glow_by_owner = glow_map
                    glow_map[owner_key] = glow_values
                except Exception:
                    pass
                proxy["current_splats"] = current
                proxy["last_signature"] = signature
                self._mgl_scene_splats[owner_key] = current
                mins = current[:, :3].min(axis=0).astype("f4")
                maxs = current[:, :3].max(axis=0).astype("f4")
                try:
                    bind_bounds = proxy.get("bind_bounds")
                    if isinstance(bind_bounds, (list, tuple)) and len(bind_bounds) >= 2:
                        bind_mins = np.asarray(bind_bounds[0], dtype="f4").reshape(-1)[:3].copy()
                        bind_maxs = np.asarray(bind_bounds[1], dtype="f4").reshape(-1)[:3].copy()
                    else:
                        bind_mins = bind_positions.min(axis=0).astype("f4")
                        bind_maxs = bind_positions.max(axis=0).astype("f4")
                        proxy["bind_bounds"] = (bind_mins, bind_maxs)
                    self._mgl_scene_splats_bounds_local[owner_key] = (bind_mins, bind_maxs)
                    self._mgl_scene_splat_bounds_by_owner[owner_key] = (mins, maxs)
                    self._mgl_scene_bounds_by_owner[owner_key] = (mins, maxs)
                except Exception:
                    pass
                changed = True
            except Exception as exc:
                try:
                    self._mgl_log_throttled(
                        "_mgl_skinned_splat_proxy_update_error_" + owner_key,
                        "skinned_splat_proxy: update failed owner=" + owner_key + " err=" + repr(exc),
                        1.0,
                    )
                except Exception:
                    pass
                continue

        if changed:
            try:
                self._mgl_render_splats = True
                self._mgl_splats_need_rebuild = True
                self._mgl_splat_force_sort = True
            except Exception:
                pass
        return bool(changed)

    @staticmethod
    def _mgl_edge_vertices_from_mesh(
        points: NDArray,
        indices: Optional[NDArray] = None,
        weld_eps: float = 1.0e-5,
    ) -> Optional[NDArray]:
        if np is None or points is None:
            return None
        try:
            pos_np = np.asarray(points, dtype="f4").reshape(-1, 3)
        except Exception:
            return None
        if pos_np.size == 0 or pos_np.shape[0] < 3:
            return np.zeros((0, 3), dtype="f4")

        if indices is None or not getattr(indices, "size", 0):
            tri_idx = np.arange(pos_np.shape[0], dtype="i4")
        else:
            try:
                tri_idx = np.asarray(indices, dtype="i4").ravel()
            except Exception:
                tri_idx = np.arange(pos_np.shape[0], dtype="i4")
        tri_count = int((tri_idx.size // 3) * 3)
        if tri_count < 3:
            return np.zeros((0, 3), dtype="f4")
        tri_idx = tri_idx[:tri_count].reshape(-1, 3)

        scale = 1.0 / max(abs(float(weld_eps)), 1.0e-8)
        canon_root: Dict[Tuple[int, int, int], int] = {}
        canon_idx = np.empty(pos_np.shape[0], dtype="i4")
        for idx, pos in enumerate(pos_np):
            key = (
                int(round(float(pos[0]) * scale)),
                int(round(float(pos[1]) * scale)),
                int(round(float(pos[2]) * scale)),
            )
            root = canon_root.get(key)
            if root is None:
                root = int(idx)
                canon_root[key] = root
            canon_idx[idx] = int(root)

        edge_keys = set()
        line_pos: List[float] = []
        for tri in tri_idx:
            try:
                a0 = int(canon_idx[int(tri[0])])
                b0 = int(canon_idx[int(tri[1])])
                c0 = int(canon_idx[int(tri[2])])
            except Exception:
                continue
            for a, b in ((a0, b0), (b0, c0), (c0, a0)):
                if a == b:
                    continue
                key = (a, b) if a < b else (b, a)
                if key in edge_keys:
                    continue
                edge_keys.add(key)
                try:
                    pa = pos_np[key[0]]
                    pb = pos_np[key[1]]
                except Exception:
                    continue
                line_pos.extend(
                    [
                        float(pa[0]),
                        float(pa[1]),
                        float(pa[2]),
                        float(pb[0]),
                        float(pb[1]),
                        float(pb[2]),
                    ]
                )
        if not line_pos:
            return np.zeros((0, 3), dtype="f4")
        return np.asarray(line_pos, dtype="f4").reshape(-1, 3)

    @staticmethod
    def _mgl_load_obj_edge_vertices(path: Path) -> NDArray:
        if np is None:
            raise RuntimeError("numpy unavailable")
        positions: List[Tuple[float, float, float]] = []
        edges = set()
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            raw = path.read_text(errors="ignore")
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if not parts:
                continue
            head = parts[0].lower()
            if head == "v" and len(parts) >= 4:
                try:
                    positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except Exception:
                    continue
                continue
            if head == "f" and len(parts) >= 3:
                face: List[int] = []
                for token in parts[1:]:
                    idx_str = token.split("/")[0] if token else ""
                    if not idx_str:
                        continue
                    try:
                        idx = int(idx_str)
                    except Exception:
                        continue
                    if idx < 0:
                        idx = len(positions) + idx + 1
                    if idx <= 0 or idx > len(positions):
                        continue
                    face.append(idx - 1)
                if len(face) < 2:
                    continue
                for i in range(len(face)):
                    a = face[i]
                    b = face[(i + 1) % len(face)]
                    if a == b:
                        continue
                    edge = (a, b) if a < b else (b, a)
                    edges.add(edge)
                continue
            if head == "l" and len(parts) >= 3:
                line_indices: List[int] = []
                for token in parts[1:]:
                    idx_str = token.split("/")[0] if token else ""
                    if not idx_str:
                        continue
                    try:
                        idx = int(idx_str)
                    except Exception:
                        continue
                    if idx < 0:
                        idx = len(positions) + idx + 1
                    if idx <= 0 or idx > len(positions):
                        continue
                    line_indices.append(idx - 1)
                if len(line_indices) < 2:
                    continue
                for i in range(len(line_indices) - 1):
                    a = line_indices[i]
                    b = line_indices[i + 1]
                    if a == b:
                        continue
                    edge = (a, b) if a < b else (b, a)
                    edges.add(edge)

        if not edges:
            return np.zeros((0, 3), dtype="f4")
        line_pos: List[float] = []
        for a, b in edges:
            try:
                ax, ay, az = positions[a]
                bx, by, bz = positions[b]
            except Exception:
                continue
            line_pos.extend([ax, ay, az, bx, by, bz])
        if not line_pos:
            return np.zeros((0, 3), dtype="f4")
        return np.array(line_pos, dtype="f4").reshape(-1, 3)

    @staticmethod
    def _mgl_load_obj_outline_vertices(path: Path, coplanar_eps: float = 0.9995, weld_eps: float = 1.0e-5) -> NDArray:
        if np is None:
            raise RuntimeError("numpy unavailable")
        positions: List[Tuple[float, float, float]] = []
        faces: List[List[int]] = []
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            raw = path.read_text(errors="ignore")
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if not parts:
                continue
            head = parts[0].lower()
            if head == "v" and len(parts) >= 4:
                try:
                    positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except Exception:
                    continue
                continue
            if head == "f" and len(parts) >= 3:
                face: List[int] = []
                for token in parts[1:]:
                    idx_str = token.split("/")[0] if token else ""
                    if not idx_str:
                        continue
                    try:
                        idx = int(idx_str)
                    except Exception:
                        continue
                    if idx < 0:
                        idx = len(positions) + idx + 1
                    if idx <= 0 or idx > len(positions):
                        continue
                    face.append(idx - 1)
                if len(face) >= 3:
                    faces.append(face)
                continue

        if not faces:
            return np.zeros((0, 3), dtype="f4")

        # Weld identical (or near-identical) positions so triangulated faces
        # don't leave internal diagonals when a mesh has duplicated vertices.
        key_to_idx = {}
        index_map: List[int] = [0] * len(positions)
        canon_positions: List[Tuple[float, float, float]] = []
        inv = 1.0 / float(weld_eps) if float(weld_eps) > 0.0 else 1.0e5
        for i, p in enumerate(positions):
            try:
                k = (int(round(p[0] * inv)), int(round(p[1] * inv)), int(round(p[2] * inv)))
            except Exception:
                k = (i, i, i)
            existing = key_to_idx.get(k)
            if existing is None:
                existing = len(canon_positions)
                key_to_idx[k] = existing
                canon_positions.append(p)
            index_map[i] = existing

        try:
            pos_np = np.array(canon_positions, dtype="f4")
        except Exception:
            return np.zeros((0, 3), dtype="f4")

        normals: List[np.ndarray] = []
        for face in faces:
            if len(face) < 3:
                normals.append(None)
                continue
            try:
                canon = [index_map[idx] for idx in face if idx is not None]
            except Exception:
                canon = []
            if len(canon) < 3:
                normals.append(None)
                continue
            # Find first 3 non-collinear vertices
            n = None
            try:
                base = pos_np[canon[0]]
                for j in range(1, len(canon) - 1):
                    a = pos_np[canon[j]]
                    b = pos_np[canon[j + 1]]
                    v1 = a - base
                    v2 = b - base
                    cand = np.cross(v1, v2)
                    ln = float(np.linalg.norm(cand))
                    if ln > 1e-8:
                        n = cand / ln
                        break
            except Exception:
                n = None
            if n is None:
                normals.append(None)
            else:
                normals.append(n.astype("f4"))

        edge_normals = {}
        for fi, face in enumerate(faces):
            n = normals[fi]
            if len(face) < 2:
                continue
            try:
                canon_face = [index_map[idx] for idx in face]
            except Exception:
                canon_face = []
            if len(canon_face) < 2:
                continue
            for i in range(len(canon_face)):
                a = canon_face[i]
                b = canon_face[(i + 1) % len(canon_face)]
                if a == b:
                    continue
                key = (a, b) if a < b else (b, a)
                entry = edge_normals.get(key)
                if entry is None:
                    edge_normals[key] = [n]
                else:
                    entry.append(n)

        line_pos: List[float] = []
        for (a, b), norms in edge_normals.items():
            keep = True
            if isinstance(norms, list) and len(norms) >= 2:
                n0 = norms[0]
                n1 = norms[1]
                if n0 is not None and n1 is not None:
                    try:
                        dot = float(np.dot(n0, n1))
                        if dot >= float(coplanar_eps):
                            keep = False
                    except Exception:
                        keep = True
            if not keep:
                continue
            try:
                ax, ay, az = pos_np[a]
                bx, by, bz = pos_np[b]
            except Exception:
                continue
            line_pos.extend([float(ax), float(ay), float(az), float(bx), float(by), float(bz)])

        if not line_pos:
            return np.zeros((0, 3), dtype="f4")
        return np.array(line_pos, dtype="f4").reshape(-1, 3)

    def _mgl_add_obj_wire_item(
        self,
        path: Path,
        visible: bool,
        tag: str = "model-wire",
        owner: Optional[str] = None,
        path_key: Optional[str] = None,
        outline_only: bool = False,
    ) -> Optional[MGLSceneItem]:
        try:
            if outline_only:
                line_points = self._mgl_load_obj_outline_vertices(path)
            else:
                line_points = self._mgl_load_obj_edge_vertices(path)
        except Exception:
            return None
        return self._mgl_add_wire_item_from_points(
            name=f"{path.name}-wire",
            line_points=line_points,
            visible=visible,
            tag=tag,
            owner=owner,
            path_key=path_key,
        )

    def _mgl_add_fbx_wire_item(
        self,
        path: Path,
        visible: bool,
        tag: str = "model-wire",
        owner: Optional[str] = None,
        path_key: Optional[str] = None,
        rig_context: Optional[dict] = None,
    ) -> Optional[MGLSceneItem]:
        if np is not None:
            context = rig_context if isinstance(rig_context, dict) else self._mgl_fbx_rig_context_for_path(path)
            if isinstance(context, dict) and context.get("skeleton") is not None:
                try:
                    sample = evaluate_skeleton_line_points(
                        context.get("skeleton"),
                        context.get("clip"),
                        self._mgl_fbx_context_timeline_sample_seconds(context, owner),
                        loop=bool(context.get("loop", True)),
                    )
                    line_points = np.array(sample.line_points or [], dtype="f4").reshape(-1, 3)
                except Exception:
                    line_points = None
                if line_points is not None and getattr(line_points, "size", 0):
                    item = self._mgl_add_wire_item_from_points(
                        name=f"{path.name}-wire",
                        line_points=line_points,
                        visible=visible,
                        tag=tag,
                        owner=owner,
                        path_key=path_key,
                    )
                    if item is not None:
                        payload = dict(item.payload or {})
                        payload["fbx_rig_context"] = context
                        payload["fbx_rig_pose_mode"] = "animated"
                        payload["_fbx_rig_frame"] = ("animated", self._mgl_timeline_frame_index())
                        item.payload = payload
                        return item
        try:
            line_points = load_fbx_edge_vertices(path)
        except Exception:
            return None
        return self._mgl_add_wire_item_from_points(
            name=f"{path.name}-wire",
            line_points=line_points,
            visible=visible,
            tag=tag,
            owner=owner,
            path_key=path_key,
        )

    def _mgl_add_fbx_joint_overlay_item(
        self,
        path: Path,
        visible: bool,
        *,
        pose_mode: str,
        owner: Optional[str] = None,
        path_key: Optional[str] = None,
        rig_context: Optional[dict] = None,
    ) -> Optional[MGLSceneItem]:
        if np is None:
            self._mgl_fbx_joints_log(
                "overlay skip reason=numpy_unavailable "
                + f"mode={pose_mode} owner={owner or ''} path={path_key or path}"
            )
            return None
        context = rig_context if isinstance(rig_context, dict) else self._mgl_fbx_rig_context_for_path(path)
        if not isinstance(context, dict):
            self._mgl_fbx_joints_log(
                "overlay skip reason=no_context "
                + f"mode={pose_mode} owner={owner or ''} path={path_key or path}"
            )
            return None
        skeleton = context.get("skeleton")
        if skeleton is None:
            self._mgl_fbx_joints_log(
                "overlay skip reason=no_skeleton "
                + f"mode={pose_mode} owner={owner or ''} path={path_key or path} "
                + f"capture={bool(context.get('show_capture_joints', False))} "
                + f"animated={bool(context.get('show_animated_joints', False))}"
            )
            return None
        mode = str(pose_mode or "animated").strip().lower()
        if mode in {"capture", "bind", "rest", "capture_pose"}:
            clip = None
            sample_time = 0.0
            loop = True
            default_color = (1.00, 0.12, 0.12, 1.0)
        else:
            mode = "animated"
            if bool(context.get("retarget_static_pose", False)):
                clip = None
                sample_time = 0.0
            else:
                clip = context.get("clip")
                sample_time = self._mgl_fbx_context_timeline_sample_seconds(context, owner)
            loop = bool(context.get("loop", True))
            default_color = (1.00, 0.95, 0.15, 1.0)
        try:
            raw_color = context.get("joint_color")
            if isinstance(raw_color, (list, tuple)) and len(raw_color) >= 3:
                default_color = (
                    float(raw_color[0]),
                    float(raw_color[1]),
                    float(raw_color[2]),
                    float(raw_color[3]) if len(raw_color) >= 4 else 1.0,
                )
        except Exception:
            pass
        line_points = self._mgl_fbx_joint_line_points(
            skeleton,
            clip,
            sample_time,
            loop=loop,
            prefer_inverse_bind=bool(mode in {"capture", "bind", "rest", "capture_pose"}),
        )
        try:
            preview_role = str(context.get("retarget_preview_role") or "").strip()
            if preview_role:
                self._mgl_retarget_log(
                    "renderer_overlay_points",
                    role=preview_role,
                    owner=str(owner or ""),
                    path=str(path_key or path),
                    mode=mode,
                    pose_mode=str(pose_mode or ""),
                    clip_name=str(getattr(clip, "name", "") or "<bind>"),
                    sample_time=round(float(sample_time), 6),
                    loop=bool(loop),
                    prefer_inverse_bind=bool(mode in {"capture", "bind", "rest", "capture_pose"}),
                    joint_count=int(len(list(getattr(skeleton, "joints", []) or []))),
                    line_points=self._mgl_retarget_points_summary(line_points),
                    context_show_capture=bool(context.get("show_capture_joints", False)),
                    context_show_animated=bool(context.get("show_animated_joints", False)),
                )
        except Exception:
            pass
        if line_points.size == 0:
            self._mgl_fbx_joints_log(
                "overlay skip reason=empty_line_points "
                + f"mode={mode} owner={owner or ''} path={path_key or path}"
            )
            return None
        name_suffix = "capture" if mode == "capture" else "animated"
        item = self._mgl_add_wire_item_from_points(
            name=f"{path.name}-joints-{name_suffix}",
            line_points=line_points,
            visible=visible,
            tag="scene-rig-joints",
            owner=owner,
            path_key=path_key,
        )
        if item is None:
            self._mgl_fbx_joints_log(
                "overlay skip reason=wire_item_failed "
                + f"mode={mode} owner={owner or ''} path={path_key or path} "
                + f"points={int(line_points.shape[0])}"
            )
            return None
        payload = dict(item.payload or {})
        line_width = 5.2
        try:
            line_width = float(context.get("joint_line_width", line_width) or line_width)
        except Exception:
            line_width = 5.2
        payload["color"] = default_color
        payload["line_width"] = max(0.5, min(20.0, float(line_width)))
        payload["xray"] = bool(context.get("joint_xray", True))
        payload["xray_back_alpha"] = 0.35
        payload["fbx_rig_context"] = context
        payload["fbx_rig_pose_mode"] = mode
        payload["_fbx_rig_frame"] = (
            ("capture", 0)
            if mode == "capture"
            else ("animated", 0 if bool(context.get("retarget_static_pose", False)) else self._mgl_timeline_frame_index())
        )
        try:
            payload["segment_count"] = int(line_points.shape[0] // 2)
            payload["bounds_min"] = line_points.min(axis=0).astype("f4")
            payload["bounds_max"] = line_points.max(axis=0).astype("f4")
        except Exception:
            pass
        item.payload = payload
        try:
            self._mgl_fbx_joints_log(
                "overlay add "
                + f"mode={mode} owner={owner or ''} path={path_key or path} "
                + f"visible={bool(visible)} segments={int(line_points.shape[0] // 2)} "
                + f"capture={bool(context.get('show_capture_joints', False))} "
                + f"animated={bool(context.get('show_animated_joints', False))}"
            )
        except Exception:
            pass
        return item

    @staticmethod
    def _mgl_casefold_get(mapping, key):
        if not isinstance(mapping, dict):
            return None
        if key in mapping:
            return mapping.get(key)
        lk = str(key or "").strip().lower()
        for k, v in mapping.items():
            try:
                if str(k).strip().lower() == lk:
                    return v
            except Exception:
                continue
        return None

    def _mgl_normalize_material(self, raw) -> Optional[dict]:
        if not isinstance(raw, dict):
            return None

        def _norm01(value, default: float) -> float:
            try:
                num = float(value)
            except Exception:
                num = float(default)
            if num > 1.0:
                num *= 0.01
            if not math.isfinite(num):
                num = float(default)
            return max(0.0, min(1.0, num))

        def _norm_ior(value, legacy_value, default: float = 1.50) -> float:
            try:
                text = str(value or "").strip()
            except Exception:
                text = ""
            if text:
                try:
                    num = float(text)
                except Exception:
                    num = float(default)
                if not math.isfinite(num):
                    num = float(default)
                return max(1.0, min(2.5, num))
            try:
                legacy = float(legacy_value)
            except Exception:
                legacy = None
            if legacy is None:
                return max(1.0, min(2.5, float(default)))
            if legacy <= 0.0:
                return 1.0
            if legacy <= 1.0:
                return max(1.0, min(2.5, 1.0 + legacy))
            return max(1.0, min(2.5, 1.0 + (legacy * 0.01)))

        def _norm_color3(value) -> tuple[float, float, float]:
            default = (1.0, 1.0, 1.0)
            if isinstance(value, str):
                text = str(value or "").strip()
                if text.startswith("#") and len(text) == 7:
                    try:
                        return (
                            int(text[1:3], 16) / 255.0,
                            int(text[3:5], 16) / 255.0,
                            int(text[5:7], 16) / 255.0,
                        )
                    except Exception:
                        return default
                return default
            if isinstance(value, (list, tuple)) and len(value) >= 3:
                try:
                    comps = [float(value[0]), float(value[1]), float(value[2])]
                except Exception:
                    return default
                if max(comps) > 1.0:
                    comps = [comp / 255.0 for comp in comps]
                return (
                    max(0.0, min(1.0, comps[0])),
                    max(0.0, min(1.0, comps[1])),
                    max(0.0, min(1.0, comps[2])),
                )
            return default

        transparency = _norm01(raw.get("transparency", 0.0), 0.0)
        ior = _norm_ior(raw.get("ior", None), raw.get("refraction", None), 1.50)
        tint_color = _norm_color3(raw.get("tint_color", raw.get("base_color", "#ffffff")))
        fresnel_amount = _norm01(raw.get("fresnel_amount", 0.0), 0.0)
        fresnel_color = _norm_color3(raw.get("fresnel_color", "#ffffff"))
        return {
            "transparency": transparency,
            "ior": ior,
            "tint_color": tint_color,
            "fresnel_amount": fresnel_amount,
            "fresnel_color": fresnel_color,
        }

    def _mgl_material_has_effect(self, material) -> bool:
        if not isinstance(material, dict):
            return False
        try:
            if float(material.get("transparency", 0.0) or 0.0) > 1e-4:
                return True
        except Exception:
            pass
        try:
            if float(material.get("ior", 1.0) or 1.0) > 1.001:
                return True
        except Exception:
            pass
        try:
            if float(material.get("fresnel_amount", 0.0) or 0.0) > 1e-4:
                return True
        except Exception:
            pass
        try:
            tint = material.get("tint_color", (1.0, 1.0, 1.0))
            return any(abs(float(comp) - 1.0) > 1e-4 for comp in tint[:3])
        except Exception:
            return False

    def _mgl_material_uses_refraction(self, material) -> bool:
        if not isinstance(material, dict):
            return False
        try:
            return bool(float(material.get("ior", 1.0) or 1.0) > 1.001)
        except Exception:
            return False

    def _mgl_material_is_transparent(self, material) -> bool:
        if not isinstance(material, dict):
            return False
        try:
            if float(material.get("transparency", 0.0) or 0.0) > 1e-4:
                return True
        except Exception:
            pass
        try:
            return bool(float(material.get("ior", 1.0) or 1.0) > 1.001)
        except Exception:
            return False

    def _mgl_scene_item_is_transparent(self, item: MGLSceneItem) -> bool:
        if item is None or not bool(getattr(item, "visible", False)):
            return False
        try:
            payload = getattr(item, "payload", None) or {}
        except Exception:
            payload = {}
        return self._mgl_material_is_transparent(payload.get("material"))

    def _mgl_owner_pivot_local(self, owner: str, bmin=None, bmax=None):
        # Optional per-owner local pivot override (used by scene cameras).
        try:
            piv_map = getattr(self, "_mgl_scene_pivot_local_by_owner", None)
            piv = self._mgl_casefold_get(piv_map, owner)
            if isinstance(piv, (list, tuple)) and len(piv) >= 3:
                return (float(piv[0]), float(piv[1]), float(piv[2]))
        except Exception:
            pass
        try:
            if bmin is not None and bmax is not None:
                c = (np.array(bmin, dtype=np.float32) + np.array(bmax, dtype=np.float32)) * 0.5
                return (float(c[0]), float(c[1]), float(c[2]))
        except Exception:
            pass
        return (0.0, 0.0, 0.0)

    def _mgl_camera_front_ring_pivot_local(self, points, bmin, bmax):
        # Camera pivot should sit on the front (min-Z) wide lens ring center.
        try:
            if np is not None and points is not None:
                arr = np.asarray(points, dtype=np.float32)
                if arr.ndim == 2 and arr.shape[1] >= 3 and arr.shape[0] > 0:
                    min_z = float(np.min(arr[:, 2]))
                    max_z = float(np.max(arr[:, 2]))
                    span_z = max(0.0, max_z - min_z)
                    eps = max(1.0e-4, span_z * 0.02)
                    front = arr[np.abs(arr[:, 2] - min_z) <= eps]
                    if front.ndim == 2 and front.shape[0] >= 3:
                        cx = float(np.mean(front[:, 0]))
                        cy = float(np.mean(front[:, 1]))
                        return (cx, cy, min_z)
        except Exception:
            pass
        try:
            cx = (float(bmin[0]) + float(bmax[0])) * 0.5
            cy = (float(bmin[1]) + float(bmax[1])) * 0.5
            cz = float(bmin[2])
            return (cx, cy, cz)
        except Exception:
            return (0.0, 0.0, 0.0)

    def _on_mgl_pick_model(self) -> None:
        if not self._use_moderngl:
            return
        if not _HAS_MGL:
            self._mgl_error = "ModernGL dependencies unavailable"
            self.update()
            return
        base = Path(__file__).resolve().parents[1] / "3dmodels"
        start_dir = str(base) if base.exists() else str(Path.home())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Model",
            start_dir,
            "Mesh files (*.obj *.gltf *.glb *.fbx *.bvh *.stl *.ply *.off *.om)",
        )
        if not path:
            return
        self._mgl_load_mesh(Path(path))
        self.update()

    def _on_mgl_grid_toggled(self, checked: bool) -> None:
        if not self._use_moderngl:
            return

        # This checkbox controls the procedural grid only
        self._mgl_grid_visible = bool(checked)
        try:
            self._mgl_log(
                "scene: grid_toggle="
                + str(bool(checked))
                + " vao="
                + str(getattr(self, "_mgl_grid_vao", None) is not None)
                + " count="
                + str(getattr(self, "_mgl_grid_vertex_count", 0))
            )
        except Exception:
            pass

        # Force-disable the legacy grid.obj grid-model system
        try:
            self._mgl_grid_model_visible = False
            self._mgl_grid_model_pending_path = None
        except Exception:
            pass

        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            try:
                scene.set_visible_by_tag("grid-model", False)
            except Exception:
                pass
            try:
                scene.remove_by_tag("grid-model")
            except Exception:
                pass

        try:
            self._mgl_clear_grid_model()
        except Exception:
            pass

        self.update()

    def _mgl_clear_grid_model(self) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is not None and scene.has_tag("grid-model"):
            scene.remove_by_tag("grid-model")
            for name in (
                "_mgl_grid_model_vao",
                "_mgl_grid_model_vbo",
                "_mgl_grid_model_nbo",
                "_mgl_grid_model_tbo",
                "_mgl_grid_model_ibo",
            ):
                setattr(self, name, None)
            self._mgl_grid_model_count = 0
            return
        for name in (
            "_mgl_grid_model_vao",
            "_mgl_grid_model_vbo",
            "_mgl_grid_model_nbo",
            "_mgl_grid_model_tbo",
            "_mgl_grid_model_ibo",
        ):
            buf = getattr(self, name, None)
            if buf is not None and hasattr(buf, "release"):
                try:
                    buf.release()
                except Exception:
                    pass
            setattr(self, name, None)
        self._mgl_grid_model_count = 0

    def _mgl_clear_scene_models(self) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            return
        for tag in (
            "model",
            "model-wire",
            "scene-model",
            "scene-wire",
            "scene-rig-joints",
            "scene-volume",
            "scene-camera",
            "scene-light",
            "scene-fx-trail",
            "retarget-handles",
            "retarget-selection",
            "retarget-links",
            "retarget-drag-link",
        ):
            scene.remove_by_tag(tag)
        try:
            self._mgl_scene_light_owner = None
            self._mgl_scene_light_type = "directional"
            self._mgl_scene_light_dir = None
            self._mgl_scene_light_pos = None
            self._mgl_scene_light_intensity = None
            self._mgl_scene_light_range = None
            self._mgl_scene_light_shadow_strength = None
            self._mgl_scene_light_shadow_range = None
            self._mgl_scene_light_shadow_fov = None
            self._mgl_scene_light_shadow_near = None
            self._mgl_scene_light_shadow_bias = None
        except Exception:
            pass
        try:
            self._mgl_shadow_dirty = True
            self._mgl_shadow_valid = False
            self._mgl_shadow_signature = None
        except Exception:
            pass

    @staticmethod
    def _mgl_retarget_parse_joint_map(raw_map) -> Dict[str, str]:
        if isinstance(raw_map, dict):
            payload = raw_map
        else:
            try:
                import json

                payload = json.loads(str(raw_map or "{}"))
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            return {}
        result: Dict[str, str] = {}
        for key, value in payload.items():
            source = str(key or "").strip()
            target = str(value or "").strip()
            if source and target:
                result[source] = target
        return result

    def _mgl_retarget_marker_line_points(self, markers, *, segments: int = 16):
        if np is None:
            return None
        seg_count = max(8, min(32, int(segments)))
        rows = []
        for marker in markers or []:
            try:
                pos, radius = marker
                cx, cy, cz = [float(v) for v in pos[:3]]
                r = max(0.001, float(radius))
            except Exception:
                continue
            for plane in ("xy", "xz", "yz"):
                prev = None
                first = None
                for idx in range(seg_count):
                    a = (float(idx) / float(seg_count)) * math.tau
                    ca = math.cos(a) * r
                    sa = math.sin(a) * r
                    if plane == "xy":
                        cur = (cx + ca, cy + sa, cz)
                    elif plane == "xz":
                        cur = (cx + ca, cy, cz + sa)
                    else:
                        cur = (cx, cy + ca, cz + sa)
                    if first is None:
                        first = cur
                    if prev is not None:
                        rows.append(prev)
                        rows.append(cur)
                    prev = cur
                if prev is not None and first is not None:
                    rows.append(prev)
                    rows.append(first)
        if not rows:
            return None
        try:
            return np.asarray(rows, dtype="f4").reshape(-1, 3)
        except Exception:
            return None

    def _mgl_retarget_sphere_arrays(
        self,
        markers,
        *,
        rings: int = 4,
        segments: int = 8,
        default_color=(1.0, 1.0, 1.0, 1.0),
    ):
        if np is None:
            return None, None, None
        ring_count = max(3, min(8, int(rings)))
        seg_count = max(6, min(16, int(segments)))
        points = []
        normals = []
        colors = []

        def _normal(theta: float, phi: float):
            ct = math.cos(theta)
            return (
                float(ct * math.cos(phi)),
                float(math.sin(theta)),
                float(ct * math.sin(phi)),
            )

        def _vertex(center, radius: float, normal):
            return (
                float(center[0]) + float(normal[0]) * radius,
                float(center[1]) + float(normal[1]) * radius,
                float(center[2]) + float(normal[2]) * radius,
            )

        for marker in markers or []:
            try:
                center, radius = marker[0], marker[1]
                c = (float(center[0]), float(center[1]), float(center[2]))
                r = max(0.001, float(radius))
            except Exception:
                continue
            try:
                raw_color = marker[2] if len(marker) >= 3 else default_color
                marker_color = (
                    float(raw_color[0]),
                    float(raw_color[1]),
                    float(raw_color[2]),
                    float(raw_color[3]) if len(raw_color) >= 4 else 1.0,
                )
            except Exception:
                marker_color = tuple(default_color)
            for lat in range(ring_count):
                theta0 = (-math.pi * 0.5) + (math.pi * float(lat) / float(ring_count))
                theta1 = (-math.pi * 0.5) + (math.pi * float(lat + 1) / float(ring_count))
                for lon in range(seg_count):
                    phi0 = math.tau * float(lon) / float(seg_count)
                    phi1 = math.tau * float(lon + 1) / float(seg_count)
                    n00 = _normal(theta0, phi0)
                    n01 = _normal(theta0, phi1)
                    n10 = _normal(theta1, phi0)
                    n11 = _normal(theta1, phi1)
                    for n in (n00, n10, n11, n00, n11, n01):
                        points.append(_vertex(c, r, n))
                        normals.append(n)
                        colors.append(marker_color)
        if not points:
            return None, None, None
        try:
            return (
                np.asarray(points, dtype="f4").reshape(-1, 3),
                np.asarray(normals, dtype="f4").reshape(-1, 3),
                np.asarray(colors, dtype="f4").reshape(-1, 4),
            )
        except Exception:
            return None, None, None

    def _mgl_retarget_remove_items_by_tag_owner(self, tag: str, owner: str | None = None) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            return
        owner_key = str(owner or "").strip().lower()
        if not owner_key:
            try:
                scene.remove_by_tag(str(tag))
            except Exception:
                pass
            return
        try:
            kept = []
            for item in scene.items():
                if str(getattr(item, "tag", "") or "") != str(tag):
                    kept.append(item)
                    continue
                payload = getattr(item, "payload", None) or {}
                item_owner = str(payload.get("owner") or "").strip().lower()
                if item_owner == owner_key:
                    try:
                        item.release()
                    except Exception:
                        pass
                else:
                    kept.append(item)
            scene._items = kept
        except Exception:
            pass

    def _mgl_retarget_build_sphere_item(
        self,
        *,
        name: str,
        owner: str,
        markers,
        color,
        tag: str,
        order: int,
    ) -> Optional[MGLSceneItem]:
        if np is None or self._mgl_ctx is None or self._mgl_prog is None:
            return None
        points, normals, colors = self._mgl_retarget_sphere_arrays(markers, default_color=color)
        if points is None or normals is None or colors is None or getattr(points, "size", 0) == 0:
            return None
        entry = self._mgl_build_mesh_entry(points, normals, colors=colors)
        if entry is None:
            return None
        resources = [
            entry.get("vao"),
            entry.get("vbo"),
            entry.get("nbo"),
            entry.get("tbo"),
            entry.get("cbo"),
            entry.get("ibo"),
        ]
        payload = {
            "vao": entry.get("vao"),
            "mesh_entry": entry,
            "texture": None,
            "color": tuple(color),
            "owner": str(owner or ""),
            "path": f"{owner}-retarget-spheres",
            "retarget_handle_mesh": True,
            "use_vertex_color": True,
        }
        return MGLSceneItem(
            name=name,
            draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
            payload=payload,
            resources=[res for res in resources if res is not None],
            visible=True,
            order=int(order),
            tag=tag,
        )

    def _mgl_retarget_rebuild_handle_mesh_for_owner(self, owner: str, handles: list, role: str) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            return
        self._mgl_retarget_remove_items_by_tag_owner("retarget-handles", owner)
        color_by_role = {
            "source": (0.10, 0.75, 1.00, 1.0),
            "target": (1.00, 0.45, 0.15, 1.0),
        }
        selected = getattr(self, "_mgl_retarget_selected_joint", None)
        selected_role = str((selected or {}).get("role") or "").strip().lower() if isinstance(selected, dict) else ""
        selected_name = str((selected or {}).get("name") or "").strip() if isinstance(selected, dict) else ""
        role_key = str(role or "").strip().lower()
        base_color = color_by_role.get(role_key, (0.9, 0.9, 0.9, 1.0))
        selected_color = (1.0, 0.95, 0.18, 1.0)
        markers = []
        for handle in list(handles or []):
            try:
                pos = handle.get("position", (0.0, 0.0, 0.0))
                radius = float(handle.get("radius", 0.05) or 0.05)
                name = str(handle.get("name") or "").strip()
                color = selected_color if selected_role == role_key and selected_name == name else base_color
                markers.append((pos, radius, color))
            except Exception:
                continue
        item = self._mgl_retarget_build_sphere_item(
            name=f"{owner} Joint Handles",
            owner=owner,
            markers=markers,
            color=base_color,
            tag="retarget-handles",
            order=17,
        )
        if item is not None:
            scene.add(item)
            try:
                xf = self._mgl_get_scene_asset_xform(owner)
                self._mgl_set_scene_asset_xform(
                    owner,
                    pos=xf.get("pos"),
                    rot=xf.get("rot"),
                    scl=xf.get("scl"),
                    apply_to_scene_models=True,
                    use_splat_xform=False,
                )
            except Exception:
                pass

    def _mgl_retarget_refresh_selection_item(self) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            return
        try:
            scene.remove_by_tag("retarget-selection")
        except Exception:
            pass
        owners = getattr(self, "_mgl_retarget_role_owners", None)
        handles_by_owner = getattr(self, "_mgl_retarget_joint_handles_by_owner", None)
        if not isinstance(owners, dict) or not isinstance(handles_by_owner, dict):
            return
        for role in ("source", "target"):
            owner = str(owners.get(role) or "").strip()
            handles = handles_by_owner.get(owner)
            if owner and isinstance(handles, list):
                self._mgl_retarget_rebuild_handle_mesh_for_owner(owner, handles, role)

    def _mgl_retarget_positions_diag(self, points) -> float:
        if np is None:
            return 0.0
        try:
            arr = np.asarray(points, dtype="f4").reshape(-1, 3)
            if arr.size == 0:
                return 0.0
            return float(np.linalg.norm(arr.max(axis=0) - arr.min(axis=0)))
        except Exception:
            return 0.0

    def _mgl_scene_skeleton_joint_owner_token(self, owner: str, joint_name: str) -> str:
        owner_key = str(owner or "").strip()
        joint_key = str(joint_name or "").strip()
        if not owner_key or not joint_key:
            return ""
        return f"{owner_key}::skeleton_joint::{joint_key}"

    def _mgl_scene_skeleton_decode_joint_owner(self, owner: str):
        text = str(owner or "").strip()
        marker = "::skeleton_joint::"
        if marker not in text:
            return None
        asset_owner, joint_name = text.split(marker, 1)
        asset_owner = asset_owner.strip()
        joint_name = joint_name.strip()
        if not asset_owner or not joint_name:
            return None
        return asset_owner, joint_name

    def _mgl_scene_skeleton_joint_label(self, owner: str) -> str:
        decoded = self._mgl_scene_skeleton_decode_joint_owner(owner)
        if not decoded:
            return str(owner or "").strip()
        asset_owner, joint_name = decoded
        return f"{asset_owner} / {joint_name}"

    def _mgl_scene_skeleton_owner_payload(self, owner: str):
        scene = getattr(self, "_mgl_scene", None)
        owner_key = str(owner or "").strip()
        if not owner_key:
            self._mgl_scene_skeleton_log("owner_payload_skip", reason="empty_owner")
            return None
        self._mgl_scene_skeleton_log("owner_payload_start", owner=owner_key, scene_ready=bool(scene is not None))
        owner_norm = owner_key.lower()
        if scene is not None:
            for tag in ("scene-model", "scene-wire", "scene-rig-joints", "model", "model-wire"):
                try:
                    items = list(scene.iter_by_tag(tag))
                except Exception:
                    items = []
                for item in items:
                    payload = getattr(item, "payload", None) or {}
                    item_owner = str(payload.get("owner") or "").strip()
                    if item_owner and item_owner.lower() != owner_norm:
                        continue
                    context = payload.get("fbx_rig_context")
                    if isinstance(context, dict) and context.get("skeleton") is not None:
                        self._mgl_scene_skeleton_log(
                            "owner_payload_found_scene_item",
                            owner=owner_key,
                            tag=tag,
                            item_name=str(getattr(item, "name", "") or ""),
                            path=str(payload.get("path") or ""),
                            visible=bool(getattr(item, "visible", True)),
                            **self._mgl_scene_skeleton_context_log_fields(context),
                        )
                        return {
                            "item": item,
                            "payload": payload,
                            "context": context,
                            "path": str(payload.get("path") or "").strip(),
                            "visible": bool(getattr(item, "visible", True)),
                        }
        for asset in list(getattr(self, "_timeline_scene_assets", None) or []):
            if not isinstance(asset, dict):
                continue
            asset_owner = str(asset.get("node") or asset.get("owner") or "").strip()
            if asset_owner and asset_owner.lower() != owner_norm:
                continue
            context = asset.get("fbx_rig_context")
            if not isinstance(context, dict):
                path_text = str(asset.get("path") or "").strip()
                if path_text.lower().endswith((".fbx", ".bvh")):
                    try:
                        context = self._mgl_fbx_rig_context_for_path(Path(path_text))
                    except Exception:
                        context = None
            if isinstance(context, dict) and context.get("skeleton") is not None:
                self._mgl_scene_skeleton_log(
                    "owner_payload_found_scene_asset",
                    owner=owner_key,
                    path=str(asset.get("path") or ""),
                    visible=bool(asset.get("visible", True)),
                    **self._mgl_scene_skeleton_context_log_fields(context),
                )
                return {
                    "item": None,
                    "payload": asset,
                    "context": context,
                    "path": str(asset.get("path") or "").strip(),
                    "visible": bool(asset.get("visible", True)),
                }
        self._mgl_scene_skeleton_log_throttled(
            f"owner_payload_missing:{owner_key.lower()}",
            "owner_payload_missing",
            interval=1.0,
            owner=owner_key,
            scene_ready=bool(scene is not None),
            asset_count=int(len(list(getattr(self, "_timeline_scene_assets", None) or []))),
        )
        return None

    def _mgl_scene_skeleton_remove_active_items(self, owner: str | None = None) -> None:
        scene = getattr(self, "_mgl_scene", None)
        owner_norm = str(owner or "").strip().lower()
        if scene is not None:
            try:
                kept = []
                for item in scene.items():
                    tag = str(getattr(item, "tag", "") or "")
                    payload = getattr(item, "payload", None) or {}
                    is_scene_skeleton = bool(payload.get("scene_skeleton_overlay")) or tag == "scene-skeleton-handles"
                    if not is_scene_skeleton:
                        kept.append(item)
                        continue
                    item_owner = str(payload.get("owner") or "").strip().lower()
                    if owner_norm and item_owner != owner_norm:
                        kept.append(item)
                        continue
                    try:
                        item.release()
                    except Exception:
                        pass
                scene._items = kept
            except Exception:
                pass
        try:
            handles_by_owner = getattr(self, "_mgl_scene_skeleton_handles_by_owner", None)
            if isinstance(handles_by_owner, dict):
                if owner_norm:
                    for key in list(handles_by_owner.keys()):
                        if str(key or "").strip().lower() == owner_norm:
                            handles_by_owner.pop(key, None)
                else:
                    handles_by_owner.clear()
        except Exception:
            pass
        try:
            frame_keys = getattr(self, "_mgl_scene_skeleton_handle_frame_keys", None)
            if isinstance(frame_keys, dict):
                if owner_norm:
                    for key in list(frame_keys.keys()):
                        if str(key or "").strip().lower() == owner_norm:
                            frame_keys.pop(key, None)
                else:
                    frame_keys.clear()
        except Exception:
            pass

    def _mgl_scene_skeleton_remove_handle_items(self, owner: str | None = None) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            return
        owner_norm = str(owner or "").strip().lower()
        try:
            kept = []
            for item in scene.items():
                tag = str(getattr(item, "tag", "") or "")
                if tag != "scene-skeleton-handles":
                    kept.append(item)
                    continue
                payload = getattr(item, "payload", None) or {}
                item_owner = str(payload.get("owner") or "").strip().lower()
                if owner_norm and item_owner != owner_norm:
                    kept.append(item)
                    continue
                try:
                    item.release()
                except Exception:
                    pass
            scene._items = kept
        except Exception:
            pass

    @staticmethod
    def _mgl_scene_skeleton_quat_to_euler_deg(q) -> Tuple[float, float, float]:
        try:
            x, y, z, w = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
        except Exception:
            return (0.0, 0.0, 0.0)
        n = math.sqrt((x * x) + (y * y) + (z * z) + (w * w))
        if n <= 1.0e-12:
            return (0.0, 0.0, 0.0)
        x, y, z, w = x / n, y / n, z / n, w / n
        sinr_cosp = 2.0 * ((w * x) + (y * z))
        cosr_cosp = 1.0 - (2.0 * ((x * x) + (y * y)))
        rx = math.atan2(sinr_cosp, cosr_cosp)
        sinp = 2.0 * ((w * y) - (z * x))
        ry = math.copysign(math.pi * 0.5, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)
        siny_cosp = 2.0 * ((w * z) + (x * y))
        cosy_cosp = 1.0 - (2.0 * ((y * y) + (z * z)))
        rz = math.atan2(siny_cosp, cosy_cosp)
        return (math.degrees(rx), math.degrees(ry), math.degrees(rz))

    @staticmethod
    def _mgl_scene_skeleton_quat_from_euler_deg(rot_deg) -> Tuple[float, float, float, float]:
        try:
            rx = math.radians(float(rot_deg[0]))
            ry = math.radians(float(rot_deg[1]))
            rz = math.radians(float(rot_deg[2]))
        except Exception:
            return (0.0, 0.0, 0.0, 1.0)
        cx, sx = math.cos(rx * 0.5), math.sin(rx * 0.5)
        cy, sy = math.cos(ry * 0.5), math.sin(ry * 0.5)
        cz, sz = math.cos(rz * 0.5), math.sin(rz * 0.5)
        x = (sx * cy * cz) - (cx * sy * sz)
        y = (cx * sy * cz) + (sx * cy * sz)
        z = (cx * cy * sz) - (sx * sy * cz)
        w = (cx * cy * cz) + (sx * sy * sz)
        n = math.sqrt((x * x) + (y * y) + (z * z) + (w * w))
        if n <= 1.0e-12:
            return (0.0, 0.0, 0.0, 1.0)
        return (x / n, y / n, z / n, w / n)

    def _mgl_scene_skeleton_track_for_joint(self, context: dict, joint_name: str):
        clip = context.get("clip") if isinstance(context, dict) else None
        target = str(joint_name or "").strip()
        if clip is None or not target:
            return None
        for track in list(getattr(clip, "tracks", []) or []):
            if str(getattr(track, "joint_name", "") or "").strip() == target:
                return track
        return None

    def _mgl_scene_skeleton_joint_values(self, owner: str, frame: int | float | None = None):
        decoded = self._mgl_scene_skeleton_decode_joint_owner(owner)
        if not decoded:
            return None
        asset_owner, joint_name = decoded
        context = self._mgl_scene_owner_fbx_rig_context(asset_owner)
        if not isinstance(context, dict):
            return None
        skeleton = context.get("skeleton")
        clip = context.get("clip")
        if skeleton is None:
            return None
        try:
            joints = list(getattr(skeleton, "joints", []) or [])
            joint_index = next(
                idx for idx, joint in enumerate(joints)
                if str(getattr(joint, "name", "") or "").strip() == joint_name
            )
        except Exception:
            return None
        try:
            if frame is None:
                sample_seconds = self._mgl_fbx_context_timeline_sample_seconds(context, asset_owner)
            else:
                fps = float(self._mgl_timeline_fps_value())
                sample_seconds = float(frame) / max(1.0e-6, fps)
                sample_seconds = clip_sample_time_from_timeline_seconds(clip, sample_seconds)
            evaluation = evaluate_rig_at_time(
                skeleton=skeleton,
                clip=clip,
                time_seconds=float(sample_seconds),
                loop=bool(context.get("loop", True)),
            )
            local = list(getattr(evaluation, "local_transforms", []) or [])[joint_index]
            xyz = tuple(float(v) for v in tuple(getattr(local, "translation", (0.0, 0.0, 0.0)))[:3])
            rxyz = self._mgl_scene_skeleton_quat_to_euler_deg(getattr(local, "rotation", (0.0, 0.0, 0.0, 1.0)))
            return xyz, rxyz
        except Exception:
            return None

    def _mgl_scene_skeleton_joint_keys_map(self, owner: str):
        decoded = self._mgl_scene_skeleton_decode_joint_owner(owner)
        if not decoded:
            return {}
        asset_owner, joint_name = decoded
        context = self._mgl_scene_owner_fbx_rig_context(asset_owner)
        if not isinstance(context, dict):
            return {}
        clip = context.get("_scene_skeleton_original_clip") or context.get("clip")
        if clip is None:
            return {}
        track = None
        for row in list(getattr(clip, "tracks", []) or []):
            if str(getattr(row, "joint_name", "") or "").strip() == joint_name:
                track = row
                break
        if track is None:
            return {}
        try:
            fps = float(self._mgl_timeline_fps_value())
        except Exception:
            fps = 24.0
        fps = max(1.0e-6, float(fps))
        try:
            start_time = float(getattr(clip, "start_time", 0.0) or 0.0)
        except Exception:
            start_time = 0.0
        keys_map: Dict[int, Dict[str, object]] = {}

        def _entry_for_time(t: float):
            frame = int(round((float(t) - start_time) * fps))
            if frame < 0:
                return None
            return keys_map.setdefault(int(frame), {"fbx_clip_key": True, "joint_key": True, "axis_mask": [False] * 6})

        for key in list(getattr(track, "translation_keys", []) or []):
            entry = _entry_for_time(float(getattr(key, "time", start_time) or start_time))
            if entry is None:
                continue
            try:
                val = tuple(getattr(key, "value", (0.0, 0.0, 0.0)))
                entry["xyz"] = [float(val[0]), float(val[1]), float(val[2])]
                mask = list(entry.get("axis_mask") or [False] * 6)
                mask[0] = mask[1] = mask[2] = True
                entry["axis_mask"] = mask
            except Exception:
                continue
        for key in list(getattr(track, "rotation_keys", []) or []):
            entry = _entry_for_time(float(getattr(key, "time", start_time) or start_time))
            if entry is None:
                continue
            try:
                entry["rxyz"] = [
                    float(v)
                    for v in self._mgl_scene_skeleton_quat_to_euler_deg(
                        getattr(key, "value", (0.0, 0.0, 0.0, 1.0))
                    )
                ]
                mask = list(entry.get("axis_mask") or [False] * 6)
                mask[3] = mask[4] = mask[5] = True
                entry["axis_mask"] = mask
            except Exception:
                continue
        return keys_map

    def _mgl_scene_skeleton_invalidate_owner(self, owner: str) -> None:
        owner_norm = str(owner or "").strip().lower()
        scene = getattr(self, "_mgl_scene", None)
        if scene is None or not owner_norm:
            return
        for tag in ("scene-model", "scene-wire", "scene-rig-joints", "scene-skeleton-handles"):
            try:
                items = list(scene.iter_by_tag(tag))
            except Exception:
                items = []
            for item in items:
                payload = getattr(item, "payload", None) or {}
                item_owner = str(payload.get("owner") or "").strip().lower()
                if item_owner != owner_norm:
                    continue
                for key in ("_fbx_rig_frame", "_fbx_skin_frame"):
                    payload.pop(key, None)
                try:
                    item.payload = payload
                except Exception:
                    pass
        frame_keys = getattr(self, "_mgl_scene_skeleton_handle_frame_keys", None)
        if isinstance(frame_keys, dict):
            frame_keys.pop(str(owner or "").strip(), None)

    def _mgl_scene_skeleton_owner_uses_splat_xform(self, owner: str) -> bool:
        owner_key = str(owner or "").strip()
        if not owner_key:
            return False
        for attr in (
            "_mgl_scene_splats",
            "_mgl_scene_splats_world",
            "_mgl_scene_splat_xforms_by_owner",
            "_mgl_scene_splats_bounds_local",
            "_mgl_scene_splat_bounds_by_owner",
            "_mgl_scene_skinned_splat_proxies_by_owner",
        ):
            try:
                data = getattr(self, attr, None)
                if not isinstance(data, dict) or not data:
                    continue
                _matched_key, value = self._mgl_lookup_owner_entry(data, owner_key)
                if value is not None:
                    return True
            except Exception:
                continue
        return False

    def _mgl_scene_skeleton_display_xform(self, owner: str):
        owner_key = str(owner or "").strip()
        use_splat_xform = False
        xform = {}
        try:
            use_splat_xform = self._mgl_scene_skeleton_owner_uses_splat_xform(owner_key)
            xform = self._mgl_get_scene_splat_xform(owner_key) if use_splat_xform else self._mgl_get_scene_asset_xform(owner_key)
        except Exception:
            use_splat_xform = False
            xform = {}
        try:
            sx, sy, sz = (xform or {}).get("scl", (1.0, 1.0, 1.0))
            scale = (float(sx), float(sy), float(sz))
        except Exception:
            scale = (1.0, 1.0, 1.0)
        try:
            if not all(math.isfinite(float(v)) for v in scale):
                scale = (1.0, 1.0, 1.0)
        except Exception:
            scale = (1.0, 1.0, 1.0)
        return scale, xform, "splat" if use_splat_xform else "mesh"

    def _mgl_scene_skeleton_display_positions(self, owner: str, positions):
        if np is None:
            return positions, {}
        owner_key = str(owner or "").strip()
        try:
            rows = np.asarray(positions, dtype=np.float32).reshape(-1, 3)
        except Exception:
            return positions, {}
        scale, xform, xform_kind = self._mgl_scene_skeleton_display_xform(owner_key)
        try:
            pos = tuple(float(v) for v in (xform or {}).get("pos", (0.0, 0.0, 0.0))[:3])
        except Exception:
            pos = (0.0, 0.0, 0.0)
        try:
            rot = tuple(float(v) for v in (xform or {}).get("rot", (0.0, 0.0, 0.0))[:3])
        except Exception:
            rot = (0.0, 0.0, 0.0)
        try:
            scl = tuple(float(v) for v in scale[:3])
        except Exception:
            scl = (1.0, 1.0, 1.0)

        pivot = np.zeros(3, dtype=np.float32)
        if xform_kind == "splat":
            try:
                piv_map = getattr(self, "_mgl_scene_pivot_local_by_owner", None)
                piv_override = self._mgl_casefold_get(piv_map, owner_key)
                if isinstance(piv_override, (list, tuple)) and len(piv_override) >= 3:
                    pivot = np.array([float(piv_override[0]), float(piv_override[1]), float(piv_override[2])], dtype=np.float32)
                else:
                    bounds_local = getattr(self, "_mgl_scene_splats_bounds_local", None)
                    _matched_key, bounds = self._mgl_lookup_owner_entry(bounds_local, owner_key)
                    if bounds is not None:
                        bmin, bmax = bounds
                        bmin = np.asarray(bmin, dtype=np.float32).reshape(-1)[:3]
                        bmax = np.asarray(bmax, dtype=np.float32).reshape(-1)[:3]
                        if bmin.shape[0] >= 3 and bmax.shape[0] >= 3:
                            pivot = ((bmin + bmax) * 0.5).astype(np.float32)
                    else:
                        pivot = rows.mean(axis=0).astype(np.float32)
            except Exception:
                try:
                    pivot = rows.mean(axis=0).astype(np.float32)
                except Exception:
                    pivot = np.zeros(3, dtype=np.float32)

        out = rows.astype(np.float32, copy=True)
        if xform_kind == "splat":
            try:
                out = out - pivot[None, :]
                out[:, 0] *= np.float32(scl[0])
                out[:, 1] *= np.float32(scl[1])
                out[:, 2] *= np.float32(scl[2])

                rx, ry, rz = rot
                if (rx != 0.0) or (ry != 0.0) or (rz != 0.0):
                    def _quat_mul(a, b):
                        ax, ay, az, aw = a
                        bx, by, bz, bw = b
                        return np.array(
                            [
                                aw * bx + ax * bw + ay * bz - az * by,
                                aw * by - ax * bz + ay * bw + az * bx,
                                aw * bz + ax * by - ay * bx + az * bw,
                                aw * bw - ax * bx - ay * by - az * bz,
                            ],
                            dtype=np.float32,
                        )

                    def _quat_from_euler_deg(rx_deg, ry_deg, rz_deg):
                        hx = math.radians(rx_deg) * 0.5
                        hy = math.radians(ry_deg) * 0.5
                        hz = math.radians(rz_deg) * 0.5
                        sxv, cxv = math.sin(hx), math.cos(hx)
                        syv, cyv = math.sin(hy), math.cos(hy)
                        szv, czv = math.sin(hz), math.cos(hz)
                        qx = np.array([sxv, 0.0, 0.0, cxv], dtype=np.float32)
                        qy = np.array([0.0, syv, 0.0, cyv], dtype=np.float32)
                        qz = np.array([0.0, 0.0, szv, czv], dtype=np.float32)
                        return _quat_mul(_quat_mul(qz, qy), qx)

                    qg = _quat_from_euler_deg(-float(rx), -float(ry), -float(rz))
                    qv = np.array([float(qg[0]), float(qg[1]), float(qg[2])], dtype=np.float32)
                    qw = float(qg[3])
                    t = 2.0 * np.cross(qv[None, :], out)
                    out = out + (qw * t) + np.cross(qv[None, :], t)

                out = out + pivot[None, :] + np.asarray(pos, dtype=np.float32).reshape(1, 3)
            except Exception:
                out = rows.astype(np.float32, copy=True)
        else:
            try:
                out[:, 0] *= np.float32(scl[0])
                out[:, 1] *= np.float32(scl[1])
                out[:, 2] *= np.float32(scl[2])
            except Exception:
                pass

        info = {
            "xform": xform,
            "xform_kind": xform_kind,
            "display_scale": [float(v) for v in scl],
            "display_pos": [float(v) for v in pos],
            "display_rot": [float(v) for v in rot],
            "display_pivot": [float(v) for v in np.asarray(pivot, dtype=np.float32).reshape(-1)[:3]],
            "display_transform": "splat_pivot_xform" if xform_kind == "splat" else "scale_only_no_translation",
        }
        return out, info

    def _mgl_scene_skeleton_apply_timeline_keys(self, owner: str, keys_map: dict | None, fps: float | None = None) -> bool:
        decoded = self._mgl_scene_skeleton_decode_joint_owner(owner)
        if not decoded:
            return False
        asset_owner, joint_name = decoded
        context = self._mgl_scene_owner_fbx_rig_context(asset_owner)
        if not isinstance(context, dict):
            return False
        original_clip = context.get("_scene_skeleton_original_clip")
        if original_clip is None:
            original_clip = context.get("clip")
        if original_clip is None:
            return False
        real_entries = {
            int(frame): dict(entry)
            for frame, entry in (keys_map or {}).items()
            if isinstance(entry, dict) and not bool(entry.get("fbx_clip_key", False))
        }
        try:
            from echograph.rigging.fbx_canonical import AnimationClip, JointAnimationTrack, QuatKeyframe, Vec3Keyframe
        except Exception:
            return False
        try:
            fps_value = float(fps if fps is not None else self._mgl_timeline_fps_value())
        except Exception:
            fps_value = 24.0
        fps_value = max(1.0e-6, float(fps_value))
        try:
            start_time = float(getattr(original_clip, "start_time", 0.0) or 0.0)
        except Exception:
            start_time = 0.0
        overrides = context.get("_scene_skeleton_joint_overrides")
        if not isinstance(overrides, dict):
            overrides = {}
        else:
            overrides = dict(overrides)
        if real_entries:
            overrides[joint_name] = real_entries
        else:
            overrides.pop(joint_name, None)
        if not overrides:
            context["_scene_skeleton_original_clip"] = original_clip
            context["_scene_skeleton_joint_overrides"] = {}
            context["clip"] = original_clip
            self._mgl_scene_skeleton_invalidate_owner(asset_owner)
            return True

        def _build_track_for_joint(source_track, override_joint: str, override_entries: dict):
            token = self._mgl_scene_skeleton_joint_owner_token(asset_owner, override_joint)
            merged = dict(self._mgl_scene_skeleton_joint_keys_map(token))
            for frame, entry in (override_entries or {}).items():
                try:
                    frame_i = int(frame)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                base = dict(merged.get(frame_i, {}) or {})
                base.update(dict(entry))
                base.pop("fbx_clip_key", None)
                base.pop("joint_key", None)
                merged[frame_i] = base
            translation_keys = []
            rotation_keys = []
            for frame in sorted(merged.keys()):
                entry = merged.get(frame)
                if not isinstance(entry, dict):
                    continue
                t = start_time + (float(frame) / fps_value)
                xyz = entry.get("xyz")
                if isinstance(xyz, (list, tuple)) and len(xyz) >= 3:
                    try:
                        translation_keys.append(
                            Vec3Keyframe(
                                time=float(t),
                                value=(float(xyz[0]), float(xyz[1]), float(xyz[2])),
                                interpolation="linear",
                            )
                        )
                    except Exception:
                        pass
                rxyz = entry.get("rxyz")
                if isinstance(rxyz, (list, tuple)) and len(rxyz) >= 3:
                    try:
                        rotation_keys.append(
                            QuatKeyframe(
                                time=float(t),
                                value=self._mgl_scene_skeleton_quat_from_euler_deg(rxyz),
                                interpolation="linear",
                            )
                        )
                    except Exception:
                        pass
            if not translation_keys and not rotation_keys:
                return None
            return JointAnimationTrack(
                joint_name=override_joint,
                translation_keys=translation_keys,
                rotation_keys=rotation_keys,
                scale_keys=list(getattr(source_track, "scale_keys", []) or []) if source_track is not None else [],
            )

        tracks = []
        replaced_overrides = set()
        for track in list(getattr(original_clip, "tracks", []) or []):
            track_joint = str(getattr(track, "joint_name", "") or "").strip()
            if track_joint in overrides:
                rebuilt = _build_track_for_joint(track, track_joint, overrides.get(track_joint) or {})
                if rebuilt is not None:
                    tracks.append(rebuilt)
                    replaced_overrides.add(track_joint)
                else:
                    tracks.append(track)
            else:
                tracks.append(track)
        for override_joint, override_entries in overrides.items():
            if override_joint in replaced_overrides:
                continue
            rebuilt = _build_track_for_joint(None, str(override_joint), override_entries or {})
            if rebuilt is not None:
                tracks.append(rebuilt)
        try:
            override_clip = AnimationClip(
                name=f"{getattr(original_clip, 'name', 'clip')}_{joint_name}_override",
                start_time=float(getattr(original_clip, "start_time", 0.0) or 0.0),
                end_time=float(getattr(original_clip, "end_time", 0.0) or 0.0),
                sample_rate_hz=float(getattr(original_clip, "sample_rate_hz", fps_value) or fps_value),
                tracks=tracks,
                metadata=dict(getattr(original_clip, "metadata", {}) or {}),
            )
        except Exception:
            return False
        context["_scene_skeleton_original_clip"] = original_clip
        context["_scene_skeleton_joint_overrides"] = overrides
        context["clip"] = override_clip
        self._mgl_scene_skeleton_invalidate_owner(asset_owner)
        return True

    def _mgl_scene_skeleton_rebuild_handle_mesh_for_owner(self, owner: str, handles: list) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            self._mgl_scene_skeleton_log_throttled(
                f"handle_mesh_skip_no_scene:{str(owner or '').strip().lower()}",
                "handle_mesh_skip_no_scene",
                interval=1.0,
                owner=str(owner or "").strip(),
                handle_count=int(len(list(handles or []))),
            )
            return
        self._mgl_scene_skeleton_remove_handle_items(owner)
        selected = str(getattr(self, "_mgl_scene_skeleton_selected_joint", "") or "").strip()
        self._mgl_scene_skeleton_log(
            "handle_mesh_skipped_screen_circles",
            owner=str(owner or "").strip(),
            handle_count=int(len(list(handles or []))),
            selected_joint=selected,
        )
        try:
            self.update()
        except Exception:
            pass

    def _mgl_scene_skeleton_update_owner_handles(self, owner: str, *, force: bool = False) -> bool:
        owner_key = str(owner or "").strip()
        if not owner_key:
            self._mgl_scene_skeleton_log_throttled("handles_skip_empty_owner", "handles_skip", interval=1.0, reason="empty_owner")
            return False
        context = self._mgl_scene_owner_fbx_rig_context(owner_key)
        if not isinstance(context, dict) or context.get("skeleton") is None:
            self._mgl_scene_skeleton_log_throttled(
                f"handles_skip_no_context:{owner_key.lower()}",
                "handles_skip",
                interval=1.0,
                owner=owner_key,
                reason="missing_rig_context_or_skeleton",
                **self._mgl_scene_skeleton_context_log_fields(context),
            )
            return False
        display_scale, xform, xform_kind = self._mgl_scene_skeleton_display_xform(owner_key)
        def _sig_tuple(raw, default):
            try:
                vals = raw if raw is not None else default
                return tuple(round(float(v), 6) for v in list(vals)[:3])
            except Exception:
                return tuple(round(float(v), 6) for v in default)

        frame_index = self._mgl_timeline_frame_index()
        frame_key = (
            owner_key,
            frame_index,
            _safe_clip_signature(context.get("clip")),
            str(getattr(self, "_mgl_scene_skeleton_selected_joint", "") or ""),
            tuple(round(float(v), 6) for v in display_scale),
            _sig_tuple((xform or {}).get("pos"), (0.0, 0.0, 0.0)),
            _sig_tuple((xform or {}).get("rot"), (0.0, 0.0, 0.0)),
            xform_kind,
        )
        frame_keys = getattr(self, "_mgl_scene_skeleton_handle_frame_keys", None)
        if not isinstance(frame_keys, dict):
            frame_keys = {}
            self._mgl_scene_skeleton_handle_frame_keys = frame_keys
        if not bool(force) and frame_keys.get(owner_key) == frame_key:
            self._mgl_scene_skeleton_log_throttled(
                f"handles_skip_same_frame:{owner_key.lower()}",
                "handles_skip_same_frame",
                interval=1.0,
                owner=owner_key,
                frame=int(frame_index),
                selected_joint=str(getattr(self, "_mgl_scene_skeleton_selected_joint", "") or ""),
            )
            return False
        positions = self._mgl_retarget_joint_positions_for_context(context, "source", owner_key)
        if not positions:
            self._mgl_scene_skeleton_log_throttled(
                f"handles_skip_no_positions:{owner_key.lower()}",
                "handles_skip",
                interval=1.0,
                owner=owner_key,
                reason="no_joint_positions",
                frame=int(frame_index),
                force=bool(force),
                **self._mgl_scene_skeleton_context_log_fields(context),
            )
            return False
        try:
            joints = list(getattr(context.get("skeleton"), "joints", []) or [])
        except Exception:
            joints = []
        if not joints:
            self._mgl_scene_skeleton_log_throttled(
                f"handles_skip_no_joints:{owner_key.lower()}",
                "handles_skip",
                interval=1.0,
                owner=owner_key,
                reason="no_joints",
                frame=int(frame_index),
                position_count=int(len(list(positions or []))),
                force=bool(force),
            )
            return False
        display_info = {}
        try:
            display_rows, display_info = self._mgl_scene_skeleton_display_positions(owner_key, positions)
            display_positions = [tuple(float(v) for v in row[:3]) for row in np.asarray(display_rows, dtype=np.float32).reshape(-1, 3)]
        except Exception:
            display_positions = []
            for pos in list(positions or []):
                try:
                    display_positions.append((float(pos[0]), float(pos[1]), float(pos[2])))
                except Exception:
                    display_positions.append((0.0, 0.0, 0.0))
            display_info = {}
        extent = max(1.0, self._mgl_retarget_positions_diag(display_positions))
        radius = max(
            0.06,
            min(120.0, float(extent) * 0.012 * _SCENE_SKELETON_HANDLE_RADIUS_SCALE),
        )
        handles = []
        for idx in range(min(len(joints), len(display_positions))):
            name = str(getattr(joints[idx], "name", "") or "").strip()
            if not name:
                continue
            pos = display_positions[idx]
            try:
                px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
            except Exception:
                continue
            handles.append(
                {
                    "role": "scene_skeleton",
                    "name": name,
                    "index": int(idx),
                    "owner": owner_key,
                    "position": (px, py, pz),
                    "radius": float(radius),
                    "pick_radius": max(float(radius) * 2.5, 0.25),
                }
            )
        if not handles:
            self._mgl_scene_skeleton_log_throttled(
                f"handles_skip_no_handles:{owner_key.lower()}",
                "handles_skip",
                interval=1.0,
                owner=owner_key,
                reason="no_valid_handles",
                frame=int(frame_index),
                joint_count=int(len(joints)),
                position_count=int(len(list(positions or []))),
                force=bool(force),
            )
            return False
        bounds_min = None
        bounds_max = None
        sample_positions = []
        try:
            arr = np.asarray([h.get("position", (0.0, 0.0, 0.0)) for h in handles], dtype=np.float32)
            bounds_min = [round(float(v), 6) for v in arr.min(axis=0).tolist()]
            bounds_max = [round(float(v), 6) for v in arr.max(axis=0).tolist()]
            sample_positions = [[round(float(v), 6) for v in h.get("position", (0.0, 0.0, 0.0))] for h in handles[:3]]
        except Exception:
            pass
        handles_by_owner = getattr(self, "_mgl_scene_skeleton_handles_by_owner", None)
        if not isinstance(handles_by_owner, dict):
            handles_by_owner = {}
            self._mgl_scene_skeleton_handles_by_owner = handles_by_owner
        handles_by_owner[owner_key] = handles
        self._mgl_scene_skeleton_rebuild_handle_mesh_for_owner(owner_key, handles)
        frame_keys[owner_key] = frame_key
        self._mgl_scene_skeleton_log(
            "handles_built",
            owner=owner_key,
            frame=int(frame_index),
            force=bool(force),
            joint_count=int(len(joints)),
            position_count=int(len(list(positions or []))),
            handle_count=int(len(handles)),
            radius=float(radius),
            selected_joint=str(getattr(self, "_mgl_scene_skeleton_selected_joint", "") or ""),
            bounds_min=bounds_min,
            bounds_max=bounds_max,
            sample_positions=sample_positions,
            xform=display_info.get("xform", xform),
            xform_kind=str(display_info.get("xform_kind", xform_kind)),
            display_scale=[round(float(v), 6) for v in (display_info.get("display_scale") or display_scale)],
            display_pos=[round(float(v), 6) for v in (display_info.get("display_pos") or [])],
            display_rot=[round(float(v), 6) for v in (display_info.get("display_rot") or [])],
            display_pivot=[round(float(v), 6) for v in (display_info.get("display_pivot") or [])],
            display_transform=str(display_info.get("display_transform") or "scale_only_no_translation"),
        )
        return True

    def _mgl_scene_skeleton_refresh_dynamic_handles(self) -> None:
        requested_owner = getattr(self, "_mgl_scene_skeleton_requested_owner", None)
        if requested_owner is not None:
            try:
                delattr(self, "_mgl_scene_skeleton_requested_owner")
            except Exception:
                self._mgl_scene_skeleton_requested_owner = None
            self._mgl_scene_skeleton_log(
                "refresh_process_request",
                owner=str(requested_owner or "").strip(),
                active_owner=str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "").strip(),
            )
            self._mgl_scene_skeleton_set_active(str(requested_owner or ""))
            return
        owner = str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "").strip()
        if owner:
            failed_owner = str(getattr(self, "_mgl_scene_skeleton_failed_owner", "") or "").strip().lower()
            if failed_owner and failed_owner == owner.lower():
                self._mgl_scene_skeleton_log_throttled(
                    f"refresh_skip_failed:{owner.lower()}",
                    "refresh_skip_failed",
                    interval=1.0,
                    owner=owner,
                )
                return
            try:
                scene = getattr(self, "_mgl_scene", None)
                has_overlay = False
                has_handles = False
                if scene is not None:
                    for item in scene.iter_by_tag("scene-rig-joints"):
                        payload = getattr(item, "payload", None) or {}
                        if (
                            bool(payload.get("scene_skeleton_overlay"))
                            and str(payload.get("owner") or "").strip().lower() == owner.lower()
                        ):
                            has_overlay = True
                            break
                    if not has_overlay:
                        for item in scene.iter_by_tag("scene-skeleton-handles"):
                            payload = getattr(item, "payload", None) or {}
                            if str(payload.get("owner") or "").strip().lower() == owner.lower():
                                has_handles = True
                                break
                if not has_overlay and not has_handles:
                    self._mgl_scene_skeleton_log_throttled(
                        f"refresh_rebuild_missing:{owner.lower()}",
                        "refresh_rebuild_missing",
                        interval=1.0,
                        owner=owner,
                        scene_ready=bool(scene is not None),
                        has_overlay=bool(has_overlay),
                        has_handles=bool(has_handles),
                    )
                    self._mgl_scene_skeleton_set_active(owner)
                    return
            except Exception:
                self._mgl_scene_skeleton_log_throttled(
                    f"refresh_probe_error:{owner.lower()}",
                    "refresh_probe_error",
                    interval=1.0,
                    owner=owner,
                )
            changed = self._mgl_scene_skeleton_update_owner_handles(owner)
            if changed:
                self._mgl_scene_skeleton_log("refresh_handles_updated", owner=owner)

    def _mgl_scene_skeleton_request_active(self, owner: str) -> bool:
        owner_key = str(owner or "").strip()
        previous = str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "").strip()
        self._mgl_scene_skeleton_log(
            "request_active",
            owner=owner_key,
            previous=previous,
        )
        self._mgl_scene_skeleton_requested_owner = owner_key
        self._mgl_scene_skeleton_failed_owner = ""
        if not owner_key or previous.lower() != owner_key.lower():
            self._mgl_scene_skeleton_selected_joint = ""
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_scene_skeleton_set_active(self, owner: str) -> bool:
        owner_key = str(owner or "").strip()
        previous = str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "").strip()
        self._mgl_scene_skeleton_log(
            "set_active_start",
            owner=owner_key,
            previous=previous,
        )
        if previous and previous.lower() != owner_key.lower():
            self._mgl_scene_skeleton_remove_active_items(previous)
        if not owner_key:
            self._mgl_scene_skeleton_active_owner = ""
            self._mgl_scene_skeleton_selected_joint = ""
            self._mgl_scene_skeleton_remove_active_items(None)
            self._mgl_scene_skeleton_log("set_active_clear", previous=previous)
            return True
        self._mgl_scene_skeleton_active_owner = owner_key
        context_info = self._mgl_scene_skeleton_owner_payload(owner_key)
        if not isinstance(context_info, dict):
            self._mgl_scene_skeleton_failed_owner = owner_key
            self._mgl_scene_skeleton_active_owner = ""
            self._mgl_scene_skeleton_log("set_active_no_context", owner=owner_key)
            return False
        context = context_info.get("context")
        path_text = str(context_info.get("path") or "").strip()
        path = Path(path_text) if path_text else Path(f"{owner_key}.fbx")
        scene = getattr(self, "_mgl_scene", None)
        self._mgl_scene_skeleton_log(
            "set_active_context",
            owner=owner_key,
            path=path_text,
            visible=bool(context_info.get("visible", True)),
            scene_ready=bool(scene is not None),
            **self._mgl_scene_skeleton_context_log_fields(context),
        )
        self._mgl_scene_skeleton_remove_active_items(owner_key)
        overlay_added = False
        if scene is not None and isinstance(context, dict):
            try:
                overlay = self._mgl_add_fbx_joint_overlay_item(
                    path,
                    True,
                    pose_mode="animated",
                    owner=owner_key,
                    path_key=path_text,
                    rig_context=context,
                )
            except Exception as exc:
                self._mgl_scene_skeleton_log(
                    "set_active_overlay_error",
                    owner=owner_key,
                    path=path_text,
                    error=repr(exc),
                )
                overlay = None
            if overlay is not None:
                payload = dict(getattr(overlay, "payload", None) or {})
                payload["scene_skeleton_overlay"] = True
                payload["ignore_owner_model"] = True
                payload["color"] = tuple(_SCENE_SKELETON_GREEN)
                try:
                    payload["line_width"] = max(
                        0.5,
                        float(payload.get("line_width", 2.0) or 2.0) * _SCENE_SKELETON_LINE_WIDTH_SCALE,
                    )
                except Exception:
                    payload["line_width"] = 1.0
                try:
                    payload["model"] = np.eye(4, dtype=np.float32)
                except Exception:
                    pass
                overlay.payload = payload
                overlay.order = 17
                scene.add(overlay)
                overlay_added = True
        else:
            self._mgl_scene_skeleton_log(
                "set_active_overlay_skip",
                owner=owner_key,
                scene_ready=bool(scene is not None),
                context_ready=bool(isinstance(context, dict)),
            )
        self._mgl_scene_skeleton_log(
            "set_active_overlay",
            owner=owner_key,
            overlay_added=bool(overlay_added),
        )
        handles_added = self._mgl_scene_skeleton_update_owner_handles(owner_key, force=True)
        if not overlay_added and not handles_added:
            self._mgl_scene_skeleton_failed_owner = owner_key
            self._mgl_scene_skeleton_active_owner = ""
            self._mgl_scene_skeleton_log(
                "set_active_failed",
                owner=owner_key,
                overlay_added=bool(overlay_added),
                handles_added=bool(handles_added),
            )
            return False
        self._mgl_scene_skeleton_log(
            "set_active_done",
            owner=owner_key,
            overlay_added=bool(overlay_added),
            handles_added=bool(handles_added),
            active_owner=str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "").strip(),
        )
        return True

    def _mgl_scene_skeleton_handle_click(self, handle: dict) -> bool:
        if not isinstance(handle, dict):
            self._mgl_scene_skeleton_log("handle_click_skip", reason="invalid_handle")
            return False
        owner = str(handle.get("owner") or "").strip()
        joint_name = str(handle.get("name") or "").strip()
        if not owner or not joint_name:
            self._mgl_scene_skeleton_log(
                "handle_click_skip",
                reason="missing_owner_or_joint",
                owner=owner,
                joint=joint_name,
            )
            return False
        self._mgl_scene_skeleton_active_owner = owner
        self._mgl_scene_skeleton_selected_joint = joint_name
        self._mgl_retarget_selected_joint = dict(handle)
        frame_keys = getattr(self, "_mgl_scene_skeleton_handle_frame_keys", None)
        if isinstance(frame_keys, dict):
            frame_keys.pop(owner, None)
        token = self._mgl_scene_skeleton_joint_owner_token(owner, joint_name)
        self._mgl_scene_skeleton_log(
            "handle_click",
            owner=owner,
            joint=joint_name,
            token=token,
            index=handle.get("index"),
        )
        try:
            self._xform_gizmo_owner = None
            self._xform_gizmo_owner_kind = None
            pos = handle.get("position", (0.0, 0.0, 0.0))
            self._xform_gizmo_pos = (float(pos[0]), float(pos[1]), float(pos[2]))
            self._xform_gizmo_pos_locked = True
        except Exception:
            pass
        try:
            scene_name = str(getattr(self, "_timeline_scene_name", "") or "").strip() or None
            set_ctx = getattr(self, "set_timeline_scene_context", None)
            if callable(set_ctx) and token:
                set_ctx(scene_name=scene_name, owner_name=token, apply_current_frame=False, load_audio=False)
            elif token:
                self._timeline_owner_name = token
        except Exception:
            try:
                self._timeline_owner_name = token
            except Exception:
                pass
        try:
            self._timeline_update_target_label()
            self._timeline_update_key_count_label()
            self._timeline_update_key_markers()
            self._timeline_refresh_coord_labels()
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_scene_skeleton_qt_overlay_data(self) -> dict | None:
        if np is None:
            return None
        owner = str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "").strip()
        if not owner:
            return None
        handles_by_owner = getattr(self, "_mgl_scene_skeleton_handles_by_owner", None)
        if not isinstance(handles_by_owner, dict) or not handles_by_owner:
            return None
        handles = None
        for key, value in handles_by_owner.items():
            try:
                if str(key or "").strip().lower() == owner.lower() and isinstance(value, list):
                    handles = value
                    break
            except Exception:
                continue
        if not handles:
            return None
        local_rows = []
        for handle in list(handles or []):
            try:
                local_rows.append(handle.get("position", (0.0, 0.0, 0.0)))
            except Exception:
                local_rows.append((0.0, 0.0, 0.0))
        try:
            local_rows = np.asarray(local_rows, dtype=np.float32).reshape(-1, 3)
        except Exception:
            return None
        if local_rows is None or getattr(local_rows, "size", 0) == 0:
            return None
        try:
            pick_rows, _radius_scale = self._mgl_retarget_pick_positions_for_owner(owner, handles)
        except Exception:
            pick_rows = local_rows
        try:
            pick_rows = np.asarray(pick_rows, dtype=np.float32).reshape(-1, 3)
        except Exception:
            pick_rows = local_rows
        if pick_rows is None or getattr(pick_rows, "size", 0) == 0:
            pick_rows = local_rows
        P = getattr(self, "_mgl_pick_proj", None)
        V = getattr(self, "_mgl_pick_view", None)
        M = getattr(self, "_mgl_pick_model", None)
        if P is None or V is None or M is None:
            return None
        try:
            view_proj = (np.asarray(P, dtype=np.float32) @ np.asarray(V, dtype=np.float32) @ np.asarray(M, dtype=np.float32)).astype(np.float32)
        except Exception:
            return None
        owner_model = None
        try:
            owner_model, _owner_radius_scale = self._mgl_retarget_owner_model_for_pick(owner)
            if owner_model is not None:
                owner_model = np.asarray(owner_model, dtype=np.float32).reshape(4, 4)
        except Exception:
            owner_model = None
        try:
            dpr = float(self.devicePixelRatioF())
        except Exception:
            try:
                dpr = float(self.devicePixelRatio())
            except Exception:
                dpr = 1.0
        dpr = max(1.0e-6, float(dpr))
        try:
            widget_w = max(1.0, float(self.width()))
            widget_h = max(1.0, float(self.height()))
            viewport_w = widget_w * dpr
            viewport_h = widget_h * dpr
        except Exception:
            return None
        active = getattr(self, "_mgl_active_viewport_rect", None)
        if not (isinstance(active, (list, tuple)) and len(active) >= 4):
            try:
                active = self._mgl_active_render_viewport()
            except Exception:
                active = None
        try:
            render_w, render_h = self._mgl_render_size()
        except Exception:
            render_w, render_h = int(viewport_w), int(viewport_h)
        if not (isinstance(active, (list, tuple)) and len(active) >= 4):
            active = (0, 0, int(render_w), int(render_h))
        try:
            scale_x = float(viewport_w) / max(1.0, float(render_w))
            scale_y = float(viewport_h) / max(1.0, float(render_h))
            ax = float(active[0]) * scale_x
            ay = float(active[1]) * scale_y
            aw = max(1.0, float(active[2]) * scale_x)
            ah = max(1.0, float(active[3]) * scale_y)
        except Exception:
            ax, ay, aw, ah = 0.0, 0.0, viewport_w, viewport_h

        def _range(vals):
            if not vals:
                return []
            try:
                return [round(float(min(vals)), 3), round(float(max(vals)), 3)]
            except Exception:
                return []

        def _project_rows(rows, matrix, label: str, *, row_vector: bool = False):
            try:
                rows_np = np.asarray(rows, dtype=np.float32).reshape(-1, 3)
                mat_np = np.asarray(matrix, dtype=np.float32).reshape(4, 4)
            except Exception:
                return None
            projected_rows = []
            xs = []
            ys = []
            zs = []
            finite_count = 0
            padding = 80.0
            for idx, pos in enumerate(rows_np):
                try:
                    p = np.array([float(pos[0]), float(pos[1]), float(pos[2]), 1.0], dtype=np.float32)
                    clip = (p @ mat_np) if row_vector else (mat_np @ p)
                    w = float(clip[3])
                    if abs(w) < 1.0e-8:
                        projected_rows.append(None)
                        continue
                    ndc = clip[:3] / w
                    if not np.all(np.isfinite(ndc)):
                        projected_rows.append(None)
                        continue
                    finite_count += 1
                    x_ndc = float(ndc[0])
                    y_ndc = float(ndc[1])
                    z_ndc = float(ndc[2])
                    device_x = ax + ((x_ndc * 0.5 + 0.5) * aw)
                    device_y = viewport_h - (ay + ((y_ndc * 0.5 + 0.5) * ah))
                    x = float(device_x) / dpr
                    y = float(device_y) / dpr
                    xs.append(x)
                    ys.append(y)
                    zs.append(z_ndc)
                    if x < -padding or x > widget_w + padding or y < -padding or y > widget_h + padding:
                        projected_rows.append(None)
                        continue
                    handle = handles[idx] if idx < len(handles) and isinstance(handles[idx], dict) else {}
                    projected_rows.append(
                        {
                            "x": x,
                            "y": y,
                            "z": z_ndc,
                            "name": str(handle.get("name") or ""),
                            "index": int(handle.get("index", idx) or idx),
                        }
                    )
                except Exception:
                    projected_rows.append(None)
            visible_count = int(len([p for p in projected_rows if isinstance(p, dict)]))
            return {
                "label": str(label or ""),
                "points": projected_rows,
                "visible_count": visible_count,
                "finite_count": int(finite_count),
                "x_range": _range(xs),
                "y_range": _range(ys),
                "z_range": _range(zs),
            }

        candidates = []
        for candidate in (
            _project_rows(pick_rows, view_proj, "pick_world_col"),
            _project_rows(pick_rows, view_proj.T, "pick_world_row", row_vector=True),
        ):
            if isinstance(candidate, dict):
                candidates.append(candidate)
        if owner_model is not None:
            for candidate in (
                _project_rows(local_rows, view_proj @ owner_model.T, "local_owner_col_t"),
                _project_rows(local_rows, view_proj @ owner_model, "local_owner_col"),
                _project_rows(local_rows, owner_model @ view_proj.T, "local_owner_row", row_vector=True),
                _project_rows(local_rows, owner_model.T @ view_proj.T, "local_owner_row_t", row_vector=True),
            ):
                if isinstance(candidate, dict):
                    candidates.append(candidate)
        fallback = _project_rows(local_rows, view_proj, "local_col")
        if isinstance(fallback, dict):
            candidates.append(fallback)
        if not candidates:
            return None
        best = max(candidates, key=lambda c: (int(c.get("visible_count", 0)), int(c.get("finite_count", 0))))
        projected = list(best.get("points") or [])

        lines = []
        try:
            context = self._mgl_scene_owner_fbx_rig_context(owner)
            joints = list(getattr((context or {}).get("skeleton"), "joints", []) or []) if isinstance(context, dict) else []
        except Exception:
            joints = []
        if joints:
            for idx, joint in enumerate(joints):
                try:
                    parent = int(getattr(joint, "parent_index", -1))
                except Exception:
                    parent = -1
                if parent < 0 or parent >= len(projected) or idx >= len(projected):
                    continue
                if projected[parent] is None or projected[idx] is None:
                    continue
                lines.append((int(parent), int(idx)))

        visible_points = [p for p in projected if isinstance(p, dict)]
        self._mgl_scene_skeleton_log_throttled(
            f"qt_overlay_data:{owner.lower()}",
            "qt_overlay_data",
            interval=1.0,
            owner=owner,
            point_count=int(len(visible_points)),
            line_count=int(len(lines)),
            projection=str(best.get("label") or ""),
            finite_count=int(best.get("finite_count", 0) or 0),
            x_range=best.get("x_range") or [],
            y_range=best.get("y_range") or [],
            z_range=best.get("z_range") or [],
            widget_size=[round(widget_w, 2), round(widget_h, 2)],
        )
        return {
            "owner": owner,
            "points": projected,
            "lines": lines,
            "selected": str(getattr(self, "_mgl_scene_skeleton_selected_joint", "") or "").strip(),
        }

    def _draw_scene_skeleton_qt_overlay(self, painter: QtGui.QPainter) -> None:
        data = self._mgl_scene_skeleton_qt_overlay_data()
        if not isinstance(data, dict):
            return
        points = list(data.get("points") or [])
        lines = list(data.get("lines") or [])
        if not points:
            return
        selected = str(data.get("selected") or "").strip()
        try:
            painter.save()
        except Exception:
            pass
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            no_pen = QtGui.QPen()
            no_pen.setStyle(QtCore.Qt.NoPen)
            base_brush = QtGui.QBrush(QtGui.QColor(0, 255, 0, 215))
            selected_brush = QtGui.QBrush(QtGui.QColor(255, 220, 0, 245))
            font = painter.font()
            try:
                font.setPointSize(max(7, int(font.pointSize() or 9) - 1))
                painter.setFont(font)
            except Exception:
                pass
            label_pen = QtGui.QPen(QtGui.QColor(0, 255, 0, 220), 1.0)
            selected_label_pen = QtGui.QPen(QtGui.QColor(255, 220, 0, 255), 1.0)
            show_joint_names = bool(getattr(self, "_mgl_scene_skeleton_show_joint_names", False))
            for point in points:
                if not isinstance(point, dict):
                    continue
                x = float(point.get("x", 0.0))
                y = float(point.get("y", 0.0))
                name = str(point.get("name") or "")
                is_selected = bool(selected and name == selected)
                radius = (3.5 if is_selected else 2.45) * _SCENE_SKELETON_SCREEN_HANDLE_RADIUS_SCALE
                painter.setPen(no_pen)
                painter.setBrush(selected_brush if is_selected else base_brush)
                painter.drawEllipse(QtCore.QPointF(x, y), radius, radius)
                if show_joint_names and name:
                    painter.setPen(selected_label_pen if is_selected else label_pen)
                    painter.drawText(QtCore.QPointF(x + radius + 3.0, y - radius - 2.0), name)
        finally:
            try:
                painter.restore()
            except Exception:
                pass

    def _mgl_retarget_inverse_bind_positions(self, skeleton):
        if np is None or skeleton is None:
            return []
        rows_tcol = []
        rows_trow = []
        for joint in list(getattr(skeleton, "joints", []) or []):
            raw_inv = tuple(getattr(joint, "inverse_bind_matrix", ()) or ())
            if len(raw_inv) != 16:
                rows_tcol.append((0.0, 0.0, 0.0))
                rows_trow.append((0.0, 0.0, 0.0))
                continue
            try:
                inv_bind = np.asarray(raw_inv, dtype="f4").reshape(4, 4)
                bind_global = np.linalg.inv(inv_bind)
                flat = bind_global.reshape(-1)
                rows_tcol.append((float(flat[3]), float(flat[7]), float(flat[11])))
                rows_trow.append((float(flat[12]), float(flat[13]), float(flat[14])))
            except Exception:
                rows_tcol.append((0.0, 0.0, 0.0))
                rows_trow.append((0.0, 0.0, 0.0))
        if self._mgl_retarget_positions_diag(rows_trow) > self._mgl_retarget_positions_diag(rows_tcol):
            return rows_trow
        return rows_tcol

    def _mgl_retarget_joint_positions_for_context(self, context: dict | None, role: str, owner: str | None = None):
        if not isinstance(context, dict):
            return []
        skeleton = context.get("skeleton")
        if skeleton is None:
            return []
        role_key = str(role or "").strip().lower()
        if role_key == "target":
            try:
                posed = self._mgl_retarget_target_pose_skeleton_for_context(context)
                if posed is not None:
                    skeleton = posed
            except Exception:
                pass
        target_pose_edit_static = bool(role_key == "target" and self._mgl_retarget_pick_mode() == "target_pose")
        target_animated = bool(
            role_key == "target"
            and context.get("retarget_preview_target_animated")
            and context.get("clip") is not None
            and not target_pose_edit_static
        )
        if role_key != "source" and not target_animated:
            rows = self._mgl_retarget_inverse_bind_positions(skeleton)
            if rows and self._mgl_retarget_positions_diag(rows) > 1.0e-7:
                return rows
        try:
            clip = context.get("clip") if (role_key == "source" or target_animated) else None
            sample_time = (
                self._mgl_fbx_context_timeline_sample_seconds(context, owner)
                if (role_key == "source" or target_animated)
                else 0.0
            )
            evaluation = evaluate_rig_at_time(
                skeleton=skeleton,
                clip=clip,
                time_seconds=float(sample_time),
                loop=bool(context.get("loop", True)),
            )
            rows_tcol = []
            rows_trow = []
            for matrix in list(getattr(evaluation, "global_matrices", []) or []):
                values = tuple(matrix or ())
                if len(values) != 16:
                    continue
                rows_tcol.append((float(values[3]), float(values[7]), float(values[11])))
                rows_trow.append((float(values[12]), float(values[13]), float(values[14])))
            if rows_tcol or rows_trow:
                return rows_trow if self._mgl_retarget_positions_diag(rows_trow) > self._mgl_retarget_positions_diag(rows_tcol) else rows_tcol
        except Exception:
            pass
        joints = list(getattr(skeleton, "joints", []) or [])
        positions = []
        for joint in joints:
            try:
                tx, ty, tz = getattr(getattr(joint, "local_bind", None), "translation", (0.0, 0.0, 0.0))
                local = (float(tx), float(ty), float(tz))
            except Exception:
                local = (0.0, 0.0, 0.0)
            try:
                raw_parent = getattr(joint, "parent_index", -1)
                parent_index = int(raw_parent) if raw_parent is not None else -1
            except Exception:
                parent_index = -1
            if 0 <= parent_index < len(positions):
                parent = positions[parent_index]
                positions.append((parent[0] + local[0], parent[1] + local[1], parent[2] + local[2]))
            else:
                positions.append(local)
        return positions

    def _mgl_retarget_update_owner_handles(self, owner: str, role: str, *, force: bool = False) -> bool:
        owner_key = str(owner or "").strip()
        role_key = str(role or "").strip().lower()
        if not owner_key or role_key not in {"source", "target"}:
            return False
        handles_by_owner = getattr(self, "_mgl_retarget_joint_handles_by_owner", None)
        if not isinstance(handles_by_owner, dict):
            return False
        handles = handles_by_owner.get(owner_key)
        if not isinstance(handles, list) or not handles:
            return False
        context = self._mgl_scene_owner_fbx_rig_context(owner_key)
        target_animated = bool(
            role_key == "target"
            and isinstance(context, dict)
            and context.get("retarget_preview_target_animated")
            and context.get("clip") is not None
        )
        frame_key = (role_key, owner_key, 0)
        if role_key == "source" or target_animated:
            frame_key = (
                role_key,
                owner_key,
                self._mgl_timeline_frame_index(),
                _safe_clip_signature(context.get("clip") if isinstance(context, dict) else None),
            )
        frame_keys = getattr(self, "_mgl_retarget_handle_frame_keys", None)
        if not isinstance(frame_keys, dict):
            frame_keys = {}
            self._mgl_retarget_handle_frame_keys = frame_keys
        if not bool(force) and frame_keys.get(owner_key) == frame_key:
            return False
        positions = self._mgl_retarget_joint_positions_for_context(context, role_key, owner_key)
        if not positions:
            return False
        role_positions = {}
        rows = []
        for handle in handles:
            try:
                idx = int(handle.get("index", -1) or -1)
            except Exception:
                idx = -1
            if idx < 0 or idx >= len(positions):
                continue
            pos = positions[idx]
            try:
                px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
            except Exception:
                continue
            handle["position"] = (px, py, pz)
            name = str(handle.get("name") or "").strip()
            if name:
                role_positions[name] = (px, py, pz)
            try:
                radius = float(handle.get("radius", 0.05) or 0.05)
            except Exception:
                radius = 0.05
            color = (0.10, 0.75, 1.00) if role_key == "source" else (1.00, 0.45, 0.15)
            rows.append([px, py, pz, color[0], color[1], color[2], 0.0, max(0.002, min(120.0, radius))])
        if not rows:
            return False
        positions_by_role = getattr(self, "_mgl_retarget_joint_positions", None)
        if not isinstance(positions_by_role, dict):
            positions_by_role = {}
            self._mgl_retarget_joint_positions = positions_by_role
        positions_by_role[role_key] = role_positions
        try:
            arr = np.asarray(rows, dtype="f4").reshape(-1, 8)
            self._mgl_scene_splats[owner_key] = arr
            self._mgl_scene_splats_bounds_local[owner_key] = (
                arr[:, :3].min(axis=0).astype("f4"),
                arr[:, :3].max(axis=0).astype("f4"),
            )
            self._mgl_scene_splat_bounds_by_owner[owner_key] = self._mgl_scene_splats_bounds_local[owner_key]
            self._mgl_scene_splats_world[owner_key] = arr[:, :3].astype(np.float32, copy=True)
            self._mgl_splats_need_rebuild = True
            self._mgl_splats_visibility_dirty = True
        except Exception:
            pass
        self._mgl_retarget_rebuild_handle_mesh_for_owner(owner_key, handles, role_key)
        frame_keys[owner_key] = frame_key
        return True

    def _mgl_retarget_refresh_dynamic_handles(self) -> None:
        if not bool(getattr(self, "_mgl_retarget_preview_active", False)):
            return
        owners = getattr(self, "_mgl_retarget_role_owners", None)
        if not isinstance(owners, dict):
            return
        changed = False
        source_owner = str(owners.get("source") or "").strip()
        if source_owner:
            changed = self._mgl_retarget_update_owner_handles(source_owner, "source") or changed
        target_owner = str(owners.get("target") or "").strip()
        if target_owner:
            changed = self._mgl_retarget_update_owner_handles(target_owner, "target") or changed
        if bool(getattr(self, "_mgl_retarget_selection_dirty", False)) or changed:
            try:
                self._mgl_retarget_refresh_selection_item()
            except Exception:
                pass
            self._mgl_retarget_selection_dirty = False
        if bool(getattr(self, "_mgl_retarget_links_dirty", False)) or changed:
            try:
                self._mgl_retarget_refresh_link_item()
            except Exception:
                pass
            self._mgl_retarget_links_dirty = False

    def _mgl_retarget_pick_mode(self) -> str:
        model = getattr(self, "_mgl_retarget_node_model", None)
        if model is None:
            node_item = getattr(self, "_mgl_retarget_node_item", None)
            model = getattr(node_item, "model", None) if node_item is not None else None
        mode = str(getattr(model, "_retarget_pick_mode", "") or "").strip().lower()
        mode = mode if mode in {"pelvis_constraint", "target_pose"} else ""
        previous = str(getattr(self, "_mgl_retarget_last_pick_mode", "") or "")
        if previous != mode:
            self._mgl_retarget_last_pick_mode = mode
            self._mgl_retarget_selected_joint = None
            self._mgl_retarget_selection_dirty = True
        return mode

    def _mgl_retarget_handle_constraint_click(self, handle: dict) -> bool:
        role = str(handle.get("role") or "").strip().lower()
        name = str(handle.get("name") or "").strip()
        if role == "scene_skeleton":
            return self._mgl_scene_skeleton_handle_click(handle)
        if role not in {"source", "target"} or not name:
            return False
        selected = getattr(self, "_mgl_retarget_selected_joint", None)
        if isinstance(selected, dict):
            selected_role = str(selected.get("role") or "").strip().lower()
            selected_name = str(selected.get("name") or "").strip()
            if selected_role == role and selected_name == name:
                self._mgl_retarget_selected_joint = None
                self._mgl_retarget_selection_dirty = True
                try:
                    self.update()
                except Exception:
                    pass
                return True
            if {selected_role, role} == {"source", "target"}:
                source = selected if selected_role == "source" else handle
                target = selected if selected_role == "target" else handle
                owners = getattr(self, "_mgl_retarget_role_owners", None)
                if isinstance(owners, dict):
                    source_owner = str(owners.get("source") or "").strip()
                    target_owner = str(owners.get("target") or "").strip()
                    if (
                        str(source.get("owner") or "").strip() != source_owner
                        or str(target.get("owner") or "").strip() != target_owner
                    ):
                        self._mgl_retarget_selected_joint = dict(handle)
                        self._mgl_retarget_selection_dirty = True
                        try:
                            self.update()
                        except Exception:
                            pass
                        return True
                source_name = str(source.get("name") or "").strip()
                target_name = str(target.get("name") or "").strip()
                if not self._mgl_retarget_set_pelvis_constraint_link(source_name, target_name):
                    return False
                self._mgl_retarget_selected_joint = None
                self._mgl_retarget_selection_dirty = True
                self._mgl_retarget_links_dirty = True
                try:
                    self.update()
                except Exception:
                    pass
                return True
        self._mgl_retarget_selected_joint = dict(handle)
        self._mgl_retarget_selection_dirty = True
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_retarget_handle_click(self, handle: dict, *, unlink: bool = False) -> bool:
        if not isinstance(handle, dict):
            return False
        role = str(handle.get("role") or "").strip().lower()
        name = str(handle.get("name") or "").strip()
        if role == "scene_skeleton":
            return self._mgl_scene_skeleton_handle_click(handle)
        if role not in {"source", "target"} or not name:
            return False
        pick_mode = self._mgl_retarget_pick_mode()
        if not bool(unlink) and pick_mode == "pelvis_constraint":
            return self._mgl_retarget_handle_constraint_click(handle)
        if not bool(unlink) and pick_mode == "target_pose":
            return self._mgl_retarget_handle_target_pose_click(handle)
        selected = getattr(self, "_mgl_retarget_selected_joint", None)
        if isinstance(selected, dict):
            selected_role = str(selected.get("role") or "").strip().lower()
            selected_name = str(selected.get("name") or "").strip()
            if bool(unlink) and {selected_role, role} == {"source", "target"}:
                source = selected if selected_role == "source" else handle
                target = selected if selected_role == "target" else handle
                source_name = str(source.get("name") or "").strip()
                target_name = str(target.get("name") or "").strip()
                mapping = getattr(self, "_mgl_retarget_joint_map", None)
                if isinstance(mapping, dict) and str(mapping.get(source_name) or "").strip() == target_name:
                    if not self._mgl_retarget_remove_joint_link(source_name, target_name):
                        return False
                    self._mgl_retarget_selected_joint = None
                    self._mgl_retarget_selection_dirty = True
                    self._mgl_retarget_links_dirty = True
                    try:
                        self.update()
                    except Exception:
                        pass
                    return True
                return False
            if selected_role == role and selected_name == name:
                self._mgl_retarget_selected_joint = None
                self._mgl_retarget_selection_dirty = True
                try:
                    self.update()
                except Exception:
                    pass
                return True
            if {selected_role, role} == {"source", "target"}:
                source = selected if selected_role == "source" else handle
                target = selected if selected_role == "target" else handle
                owners = getattr(self, "_mgl_retarget_role_owners", None)
                if isinstance(owners, dict):
                    source_owner = str(owners.get("source") or "").strip()
                    target_owner = str(owners.get("target") or "").strip()
                    if (
                        str(source.get("owner") or "").strip() != source_owner
                        or str(target.get("owner") or "").strip() != target_owner
                    ):
                        self._mgl_retarget_selected_joint = dict(handle)
                        self._mgl_retarget_selection_dirty = True
                        try:
                            self.update()
                        except Exception:
                            pass
                        return True
                self._mgl_retarget_set_joint_link(
                    str(source.get("name") or ""),
                    str(target.get("name") or ""),
                )
                self._mgl_retarget_selected_joint = None
                self._mgl_retarget_selection_dirty = True
                self._mgl_retarget_links_dirty = True
                try:
                    self.update()
                except Exception:
                    pass
                return True
        if bool(unlink):
            return False
        self._mgl_retarget_selected_joint = dict(handle)
        self._mgl_retarget_selection_dirty = True
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_retarget_load_preview_asset(self, asset: dict):
        if np is None or not isinstance(asset, dict):
            return None
        source_owner = str(asset.get("source_owner") or "Retarget Source Handles").strip()
        target_owner = str(asset.get("target_owner") or "Retarget Target Handles").strip()
        try:
            handle_radius = float(asset.get("handle_radius", 0.008) or 0.008)
        except Exception:
            handle_radius = 0.008
        handle_radius = max(0.002, min(120.0, handle_radius))
        try:
            curve_thickness = float(asset.get("curve_thickness", 2.4) or 2.4)
        except Exception:
            curve_thickness = 2.4
        curve_thickness = max(0.5, min(20.0, curve_thickness))
        color_by_role = {
            "source": (0.10, 0.75, 1.00, 0.92),
            "target": (1.00, 0.45, 0.15, 0.92),
        }
        owner_by_role = {"source": source_owner, "target": target_owner}
        try:
            piv = getattr(self, "_mgl_scene_pivot_local_by_owner", None)
            if not isinstance(piv, dict):
                piv = {}
            for owner_name in (source_owner, target_owner):
                if owner_name:
                    # Retarget previews must stay in their imported rig space.
                    # The default owner pivot is the bounds center, which visually
                    # recenters rigs and can split raw joint positions from handles.
                    piv[str(owner_name)] = (0.0, 0.0, 0.0)
            self._mgl_scene_pivot_local_by_owner = piv
            self._mgl_retarget_log(
                "renderer_retarget_origin_pivot",
                source_owner=source_owner,
                target_owner=target_owner,
            )
        except Exception:
            pass
        scene = getattr(self, "_mgl_scene", None)
        self._mgl_retarget_role_owners = dict(owner_by_role)
        self._mgl_retarget_handle_frame_keys = {}

        handles_by_owner: Dict[str, list] = {}
        positions_by_role: Dict[str, Dict[str, Tuple[float, float, float]]] = {"source": {}, "target": {}}
        bounds_min = None
        bounds_max = None

        def _merge(bmin, bmax, points):
            try:
                arr = np.asarray(points, dtype="f4").reshape(-1, 3)
                if arr.size == 0:
                    return bmin, bmax
                pmin = arr.min(axis=0).astype("f4")
                pmax = arr.max(axis=0).astype("f4")
                if bmin is None or bmax is None:
                    return pmin, pmax
                return np.minimum(bmin, pmin), np.maximum(bmax, pmax)
            except Exception:
                return bmin, bmax

        for role in ("source", "target"):
            owner = owner_by_role[role]
            rows = []
            metadata = []
            for raw in list(asset.get(f"{role}_handles") or []):
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name") or "").strip()
                pos = raw.get("position")
                if not name or not isinstance(pos, (list, tuple)) or len(pos) < 3:
                    continue
                try:
                    px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
                except Exception:
                    continue
                try:
                    raw_radius = float(raw.get("radius", handle_radius) or handle_radius)
                except Exception:
                    raw_radius = handle_radius
                raw_radius = max(0.002, min(120.0, raw_radius))
                rgba = color_by_role[role]
                rows.append([px, py, pz, rgba[0], rgba[1], rgba[2], 0.0, raw_radius])
                meta = {
                    "role": role,
                    "name": name,
                    "index": int(raw.get("index", len(metadata)) or 0),
                    "owner": owner,
                    "position": (px, py, pz),
                    "radius": raw_radius,
                    "pick_radius": max(raw_radius * 2.5, 0.25),
                }
                metadata.append(meta)
                positions_by_role[role][name] = (px, py, pz)
            if not rows:
                continue
            arr = np.asarray(rows, dtype="f4").reshape(-1, 8)
            try:
                self._mgl_scene_splats[owner] = arr
                self._mgl_scene_splats_bounds_local[owner] = (
                    arr[:, :3].min(axis=0).astype("f4"),
                    arr[:, :3].max(axis=0).astype("f4"),
                )
                self._mgl_scene_splat_bounds_by_owner[owner] = self._mgl_scene_splats_bounds_local[owner]
            except Exception:
                pass
            handles_by_owner[owner] = metadata
            bounds_min, bounds_max = _merge(bounds_min, bounds_max, arr[:, :3])
            if scene is not None:
                self._mgl_retarget_rebuild_handle_mesh_for_owner(owner, metadata, role)

        self._mgl_retarget_preview_active = bool(handles_by_owner)
        self._mgl_retarget_preview_owner = str(asset.get("node") or "Anim Retarget Mapping")
        self._mgl_retarget_node_item = asset.get("retarget_node_item")
        self._mgl_retarget_node_model = asset.get("retarget_node_model")
        self._mgl_retarget_joint_handles_by_owner = handles_by_owner
        self._mgl_retarget_joint_positions = positions_by_role
        self._mgl_retarget_joint_map = self._mgl_retarget_parse_joint_map(asset.get("joint_map"))
        self._mgl_retarget_handle_radius = handle_radius
        self._mgl_retarget_curve_thickness = curve_thickness
        self._mgl_retarget_selected_joint = None
        self._mgl_retarget_last_pick_mode = ""
        self._mgl_retarget_selection_dirty = True
        self._mgl_retarget_links_dirty = True
        try:
            self._mgl_retarget_update_owner_handles(source_owner, "source", force=True)
            self._mgl_retarget_update_owner_handles(target_owner, "target", force=True)
        except Exception:
            pass
        self._mgl_retarget_log(
            "renderer_load_preview_handles",
            preview_owner=self._mgl_retarget_preview_owner,
            source_owner=source_owner,
            target_owner=target_owner,
            handle_radius=round(float(handle_radius), 6),
            curve_thickness=round(float(curve_thickness), 6),
            source_handle_count=len(handles_by_owner.get(source_owner, []) or []),
            target_handle_count=len(handles_by_owner.get(target_owner, []) or []),
            source_handles=self._mgl_retarget_points_summary(list(positions_by_role.get("source", {}).values())),
            target_handles=self._mgl_retarget_points_summary(list(positions_by_role.get("target", {}).values())),
        )
        try:
            self._mgl_retarget_refresh_link_item()
        except Exception:
            pass
        if bounds_min is None or bounds_max is None:
            return None
        return bounds_min, bounds_max

    def _mgl_retarget_link_points(self, p0, p1, dash_length: float):
        if np is None:
            return []
        a = np.asarray(p0, dtype="f4").reshape(3)
        b = np.asarray(p1, dtype="f4").reshape(3)
        try:
            delta = b - a
            distance = float(np.linalg.norm(delta))
        except Exception:
            distance = 0.0
        if distance < 1.0e-6:
            return []
        try:
            desired_dash = max(0.02, float(dash_length))
        except Exception:
            desired_dash = 0.12
        period = max(desired_dash * 1.7, 1.0e-5)
        dash_count = max(1, min(96, int(math.ceil(distance / period))))
        if dash_count <= 1:
            return [a, b]
        direction = (delta / distance).astype("f4")
        period = distance / float(dash_count)
        visible_len = max(period * 0.58, period * 0.1)
        rows = []
        for index in range(dash_count):
            start_dist = period * float(index)
            end_dist = distance if index == dash_count - 1 else min(distance, start_dist + visible_len)
            if end_dist <= start_dist:
                continue
            rows.append((a + direction * start_dist).astype("f4"))
            rows.append((a + direction * end_dist).astype("f4"))
        return rows

    def _mgl_retarget_visible_position_map(self, role: str) -> Dict[str, Tuple[float, float, float]]:
        if np is None:
            return {}
        role_key = str(role or "").strip().lower()
        if role_key not in {"source", "target"}:
            return {}
        owners = getattr(self, "_mgl_retarget_role_owners", None)
        handles_by_owner = getattr(self, "_mgl_retarget_joint_handles_by_owner", None)
        if not isinstance(owners, dict) or not isinstance(handles_by_owner, dict):
            return {}
        owner = str(owners.get(role_key) or "").strip()
        handles = handles_by_owner.get(owner)
        if not owner or not isinstance(handles, list) or not handles:
            return {}
        positions, _radius_scale = self._mgl_retarget_pick_positions_for_owner(owner, handles)
        rows: Dict[str, Tuple[float, float, float]] = {}
        for index, handle in enumerate(handles):
            if index >= int(positions.shape[0]):
                continue
            name = str((handle or {}).get("name") or "").strip()
            if not name:
                continue
            try:
                pos = positions[index]
                rows[name] = (float(pos[0]), float(pos[1]), float(pos[2]))
            except Exception:
                continue
        return rows

    def _mgl_retarget_grid_min_render_size(self) -> float:
        if np is None or not bool(getattr(self, "_mgl_retarget_preview_active", False)):
            return 0.0
        handles_by_owner = getattr(self, "_mgl_retarget_joint_handles_by_owner", None)
        if not isinstance(handles_by_owner, dict) or not handles_by_owner:
            return 0.0
        chunks = []
        for owner, handles in handles_by_owner.items():
            if not isinstance(handles, list) or not handles:
                continue
            try:
                positions, _radius_scale = self._mgl_retarget_pick_positions_for_owner(str(owner), handles)
                if positions is not None and getattr(positions, "size", 0):
                    chunks.append(np.asarray(positions, dtype="f4").reshape(-1, 3))
            except Exception:
                continue
        if not chunks:
            return 0.0
        try:
            points = np.concatenate(chunks, axis=0).astype("f4", copy=False)
            if points.size == 0:
                return 0.0
            x_vals = points[:, 0]
            z_vals = points[:, 2]
            max_abs = max(
                float(np.max(np.abs(x_vals))),
                float(np.max(np.abs(z_vals))),
            )
            span = max(
                float(np.max(x_vals) - np.min(x_vals)),
                float(np.max(z_vals) - np.min(z_vals)),
            )
            return max(0.0, max_abs, span * 0.55) * 1.35
        except Exception:
            return 0.0

    def _mgl_retarget_refresh_link_item(self) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None or np is None:
            return
        try:
            scene.remove_by_tag("retarget-links")
        except Exception:
            pass
        mapping = getattr(self, "_mgl_retarget_joint_map", None)
        if not isinstance(mapping, dict) or not mapping:
            return
        source_positions = self._mgl_retarget_visible_position_map("source")
        target_positions = self._mgl_retarget_visible_position_map("target")
        if not source_positions or not target_positions:
            positions = getattr(self, "_mgl_retarget_joint_positions", None)
            if not isinstance(positions, dict):
                return
            source_positions = positions.get("source") if isinstance(positions.get("source"), dict) else {}
            target_positions = positions.get("target") if isinstance(positions.get("target"), dict) else {}
        if not source_positions or not target_positions:
            return

        all_pos = []
        for role_positions in (source_positions, target_positions):
            for pos in role_positions.values():
                try:
                    all_pos.append(np.asarray(pos, dtype="f4").reshape(3))
                except Exception:
                    pass
        if all_pos:
            stack = np.asarray(all_pos, dtype="f4").reshape(-1, 3)
            try:
                link_diag = float(np.linalg.norm(stack.max(axis=0) - stack.min(axis=0)))
            except Exception:
                link_diag = 1.0
        else:
            link_diag = 1.0
        dash_length = max(0.04, min(12.0, float(link_diag) * 0.018))

        rows = []
        for source_name, target_name in mapping.items():
            p0 = source_positions.get(str(source_name))
            p1 = target_positions.get(str(target_name))
            if p0 is None or p1 is None:
                continue
            rows.extend(self._mgl_retarget_link_points(p0, p1, dash_length))
        if not rows:
            return
        try:
            line_points = np.asarray(rows, dtype="f4").reshape(-1, 3)
        except Exception:
            return
        item = self._mgl_add_wire_item_from_points(
            name="anim-retarget-links",
            line_points=line_points,
            visible=True,
            tag="retarget-links",
            owner=str(getattr(self, "_mgl_retarget_preview_owner", "") or "Anim Retarget Mapping"),
            path_key="anim-retarget-links",
        )
        if item is None:
            return
        payload = dict(item.payload or {})
        try:
            line_width = float(getattr(self, "_mgl_retarget_curve_thickness", 2.4) or 2.4)
        except Exception:
            line_width = 2.4
        payload["color"] = (0.25, 1.00, 0.45, 1.0)
        payload["line_width"] = max(0.5, min(20.0, line_width))
        payload["xray"] = False
        item.payload = payload
        item.order = 18
        scene.add(item)
        try:
            now = float(time.time())
            last = float(getattr(self, "_mgl_retarget_link_log_ts", 0.0) or 0.0)
            if (now - last) > 1.0:
                setattr(self, "_mgl_retarget_link_log_ts", now)
                self._mgl_retarget_log(
                    "renderer_link_positions",
                    link_style="straight_dashed",
                    dash_length=round(float(dash_length), 6),
                    mapping_count=int(len(mapping)),
                    source=self._mgl_retarget_points_summary(source_positions.values()),
                    target=self._mgl_retarget_points_summary(target_positions.values()),
                )
        except Exception:
            pass

    def _mgl_retarget_set_joint_link(self, source_joint: str, target_joint: str) -> bool:
        source = str(source_joint or "").strip()
        target = str(target_joint or "").strip()
        if not source or not target:
            return False
        node_item = getattr(self, "_mgl_retarget_node_item", None)
        mapping = None
        if node_item is not None:
            try:
                from nodes.anim_retarget import spec as anim_retarget_spec  # type: ignore

                mapping = anim_retarget_spec.set_joint_map_link(
                    node_item,
                    source,
                    target,
                    notify_scene=False,
                )
            except Exception:
                mapping = None
        if not isinstance(mapping, dict):
            mapping = dict(getattr(self, "_mgl_retarget_joint_map", None) or {})
            mapping[source] = target
        self._mgl_retarget_joint_map = mapping
        self._mgl_retarget_links_dirty = True
        try:
            self._mgl_retarget_refresh_link_item()
        except Exception:
            pass
        self._mgl_retarget_notify_mapping_changed()
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_retarget_notify_mapping_changed(self) -> None:
        model = getattr(self, "_mgl_retarget_node_model", None)
        if model is None:
            node_item = getattr(self, "_mgl_retarget_node_item", None)
            model = getattr(node_item, "model", None) if node_item is not None else None
        callback = getattr(model, "_retarget_mapping_table_refresh", None) if model is not None else None
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    def _mgl_retarget_notify_target_pose_changed(self) -> None:
        model = getattr(self, "_mgl_retarget_node_model", None)
        if model is None:
            node_item = getattr(self, "_mgl_retarget_node_item", None)
            model = getattr(node_item, "model", None) if node_item is not None else None
        callback = getattr(model, "_retarget_target_pose_table_refresh", None) if model is not None else None
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    def _mgl_retarget_node_model_for_pose(self):
        model = getattr(self, "_mgl_retarget_node_model", None)
        if model is None:
            node_item = getattr(self, "_mgl_retarget_node_item", None)
            model = getattr(node_item, "model", None) if node_item is not None else None
        return model

    def _mgl_retarget_target_pose_skeleton_for_context(self, context: dict | None):
        if not isinstance(context, dict):
            return None
        skeleton = context.get("skeleton")
        if skeleton is None:
            return None
        try:
            from nodes.anim_retarget import spec as anim_retarget_spec  # type: ignore

            pose_fn = getattr(anim_retarget_spec, "target_pose_skeleton_for_model", None)
            if callable(pose_fn):
                return pose_fn(self._mgl_retarget_node_model_for_pose(), skeleton)
        except Exception:
            return skeleton
        return skeleton

    def _mgl_retarget_target_owner(self) -> str:
        owners = getattr(self, "_mgl_retarget_role_owners", None)
        if not isinstance(owners, dict):
            return ""
        return str(owners.get("target") or "").strip()

    def _mgl_retarget_target_pose_gizmo_owner(self, joint_name: str) -> str:
        name = str(joint_name or "").strip()
        return f"retarget-target-pose::{name}" if name else ""

    def _mgl_retarget_target_pose_joint_from_owner(self, owner: str) -> str:
        text = str(owner or "").strip()
        prefix = "retarget-target-pose::"
        if not text.startswith(prefix):
            return ""
        return text[len(prefix):].strip()

    def _mgl_retarget_owner_is_target_pose_joint(self, owner: str) -> bool:
        return bool(self._mgl_retarget_target_pose_joint_from_owner(owner))

    def _mgl_retarget_target_pose_world_to_local(self, world_pos) -> Tuple[float, float, float]:
        try:
            wx, wy, wz = float(world_pos[0]), float(world_pos[1]), float(world_pos[2])
        except Exception:
            return (0.0, 0.0, 0.0)
        if np is None:
            return (wx, wy, wz)
        target_owner = self._mgl_retarget_target_owner()
        model, _scale = self._mgl_retarget_owner_model_for_pick(target_owner)
        if model is None:
            return (wx, wy, wz)
        try:
            inv_model = np.linalg.inv(np.asarray(model, dtype=np.float32).reshape(4, 4))
            p4 = np.array([wx, wy, wz, 1.0], dtype=np.float32) @ inv_model
            w = float(p4[3])
            if abs(w) > 1.0e-8:
                p4 = p4 / w
            return (float(p4[0]), float(p4[1]), float(p4[2]))
        except Exception:
            return (wx, wy, wz)

    def _mgl_retarget_target_pose_joint_world_position(self, joint_name: str):
        if np is None:
            return None
        name = str(joint_name or "").strip()
        if not name:
            return None
        target_owner = self._mgl_retarget_target_owner()
        handles_by_owner = getattr(self, "_mgl_retarget_joint_handles_by_owner", None)
        if not target_owner or not isinstance(handles_by_owner, dict):
            return None
        handles = handles_by_owner.get(target_owner)
        if not isinstance(handles, list) or not handles:
            return None
        positions, _radius_scale = self._mgl_retarget_pick_positions_for_owner(target_owner, handles)
        for index, handle in enumerate(handles):
            if str((handle or {}).get("name") or "").strip() != name:
                continue
            if index >= int(positions.shape[0]):
                return None
            pos = positions[index]
            return (float(pos[0]), float(pos[1]), float(pos[2]))
        return None

    def _mgl_retarget_refresh_target_pose_contexts(self) -> None:
        target_owner = self._mgl_retarget_target_owner()
        if not target_owner:
            return
        context = self._mgl_scene_owner_fbx_rig_context(target_owner)
        if isinstance(context, dict):
            posed = self._mgl_retarget_target_pose_skeleton_for_context(context)
            if posed is not None:
                try:
                    context["skeleton"] = posed
                except Exception:
                    pass

        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            return
        owner_key = target_owner.lower()
        for tag in ("scene-model", "scene-wire", "scene-rig-joints", "model"):
            try:
                items = list(scene.iter_by_tag(tag))
            except Exception:
                items = []
            for item in items:
                payload = dict(getattr(item, "payload", None) or {})
                payload_owner = str(payload.get("owner") or "").strip()
                if not payload_owner or payload_owner.lower() != owner_key:
                    continue
                item_context = payload.get("fbx_rig_context")
                if isinstance(context, dict):
                    payload["fbx_rig_context"] = context
                elif isinstance(item_context, dict):
                    posed = self._mgl_retarget_target_pose_skeleton_for_context(item_context)
                    if posed is not None:
                        item_context["skeleton"] = posed
                if str(getattr(item, "tag", "") or "") == "scene-rig-joints":
                    payload.pop("_fbx_rig_frame", None)
                try:
                    item.payload = payload
                except Exception:
                    pass

    def _mgl_retarget_refresh_target_pose_handles(self) -> None:
        target_owner = self._mgl_retarget_target_owner()
        if not target_owner:
            return
        self._mgl_retarget_refresh_target_pose_contexts()
        try:
            self._mgl_retarget_update_owner_handles(target_owner, "target", force=True)
        except Exception:
            pass
        self._mgl_retarget_selection_dirty = True
        self._mgl_retarget_links_dirty = True
        try:
            self._mgl_retarget_refresh_selection_item()
        except Exception:
            pass
        try:
            self._mgl_retarget_refresh_link_item()
        except Exception:
            pass

    def _mgl_retarget_handle_target_pose_click(self, handle: dict) -> bool:
        role = str(handle.get("role") or "").strip().lower()
        name = str(handle.get("name") or "").strip()
        if role != "target" or not name:
            return False
        self._mgl_retarget_selected_joint = dict(handle)
        self._mgl_retarget_selection_dirty = True
        node_item = getattr(self, "_mgl_retarget_node_item", None)
        if node_item is not None:
            try:
                from nodes.anim_retarget import spec as anim_retarget_spec  # type: ignore

                set_selected = getattr(anim_retarget_spec, "set_target_pose_selected_joint", None)
                if callable(set_selected):
                    set_selected(node_item, name, notify_scene=False)
            except Exception:
                pass
        owner = self._mgl_retarget_target_pose_gizmo_owner(name)
        if owner:
            self._xform_gizmo_owner = owner
            self._xform_gizmo_owner_kind = "retarget_target_joint"
            pos = handle.get("position") or self._mgl_retarget_target_pose_joint_world_position(name) or (0.0, 0.0, 0.0)
            try:
                self._xform_gizmo_pos = (float(pos[0]), float(pos[1]), float(pos[2]))
            except Exception:
                self._xform_gizmo_pos = (0.0, 0.0, 0.0)
            self._xform_gizmo_pos_locked = True
            try:
                rot_cache = getattr(self, "_rot_owner_quat", None)
                if isinstance(rot_cache, dict):
                    rot_cache.pop(owner, None)
            except Exception:
                pass
        self._mgl_retarget_notify_target_pose_changed()
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_retarget_apply_target_pose_joint_position(self, owner: str, world_pos, *, notify_scene: bool = False) -> bool:
        joint_name = self._mgl_retarget_target_pose_joint_from_owner(owner)
        if not joint_name:
            return False
        node_item = getattr(self, "_mgl_retarget_node_item", None)
        if node_item is None:
            return False
        local_pos = self._mgl_retarget_target_pose_world_to_local(world_pos)
        try:
            from nodes.anim_retarget import spec as anim_retarget_spec  # type: ignore

            set_pos = getattr(anim_retarget_spec, "set_target_pose_joint_position", None)
            if callable(set_pos):
                set_pos(node_item, joint_name, local_pos, notify_scene=False)
        except Exception:
            return False
        self._mgl_retarget_refresh_target_pose_handles()
        new_world = self._mgl_retarget_target_pose_joint_world_position(joint_name)
        if new_world is None:
            new_world = world_pos
        try:
            self._xform_gizmo_pos = (float(new_world[0]), float(new_world[1]), float(new_world[2]))
        except Exception:
            pass
        self._mgl_retarget_notify_target_pose_changed()
        if bool(notify_scene):
            self._mgl_retarget_commit_target_pose_edit(notify_scene=True)
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_retarget_set_target_pose_joint_rotation(self, owner: str, rot_deg, *, notify_scene: bool = False) -> bool:
        joint_name = self._mgl_retarget_target_pose_joint_from_owner(owner)
        if not joint_name:
            return False
        node_item = getattr(self, "_mgl_retarget_node_item", None)
        if node_item is None:
            return False
        try:
            from nodes.anim_retarget import spec as anim_retarget_spec  # type: ignore

            set_rot = getattr(anim_retarget_spec, "set_target_pose_joint_rotation", None)
            if callable(set_rot):
                set_rot(node_item, joint_name, rot_deg, notify_scene=False)
        except Exception:
            return False
        self._mgl_retarget_refresh_target_pose_handles()
        new_world = self._mgl_retarget_target_pose_joint_world_position(joint_name)
        if new_world is not None:
            try:
                self._xform_gizmo_pos = (float(new_world[0]), float(new_world[1]), float(new_world[2]))
            except Exception:
                pass
        self._mgl_retarget_notify_target_pose_changed()
        if bool(notify_scene):
            self._mgl_retarget_commit_target_pose_edit(notify_scene=True)
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_retarget_get_target_pose_joint_rotation(self, owner: str) -> Tuple[float, float, float]:
        joint_name = self._mgl_retarget_target_pose_joint_from_owner(owner)
        if not joint_name:
            return (0.0, 0.0, 0.0)
        try:
            from nodes.anim_retarget import spec as anim_retarget_spec  # type: ignore

            payload_fn = getattr(anim_retarget_spec, "_target_pose_offsets_payload", None)
            payload = payload_fn(self._mgl_retarget_node_model_for_pose()) if callable(payload_fn) else {}
            row = payload.get(joint_name) if isinstance(payload, dict) else None
            raw = row.get("rotation") if isinstance(row, dict) else None
            if isinstance(raw, (list, tuple)) and len(raw) >= 3:
                return (float(raw[0]), float(raw[1]), float(raw[2]))
        except Exception:
            pass
        return (0.0, 0.0, 0.0)

    def _mgl_retarget_commit_target_pose_edit(self, *, notify_scene: bool = True) -> None:
        self._mgl_retarget_refresh_target_pose_contexts()
        self._mgl_retarget_notify_target_pose_changed()
        if not bool(notify_scene):
            return
        node_item = getattr(self, "_mgl_retarget_node_item", None)
        model = getattr(node_item, "model", None) if node_item is not None else self._mgl_retarget_node_model_for_pose()
        try:
            scene = node_item.scene() if node_item is not None else None
        except Exception:
            scene = None
        if scene is not None and hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.emit(getattr(model, "name", "") or "", list(getattr(model, "params", []) or []))
            except Exception:
                pass

    def _mgl_retarget_set_pelvis_constraint_link(self, source_joint: str, target_joint: str) -> bool:
        source = str(source_joint or "").strip()
        target = str(target_joint or "").strip()
        if not source or not target:
            return False
        node_item = getattr(self, "_mgl_retarget_node_item", None)
        mapping = None
        payload = None
        if node_item is not None:
            try:
                from nodes.anim_retarget import spec as anim_retarget_spec  # type: ignore

                set_link = getattr(anim_retarget_spec, "set_joint_map_link", None)
                if callable(set_link):
                    mapping = set_link(
                        node_item,
                        source,
                        target,
                        notify_scene=False,
                    )
                set_constraint = getattr(anim_retarget_spec, "set_pelvis_constraint_link", None)
                if callable(set_constraint):
                    payload = set_constraint(
                        node_item,
                        source,
                        target,
                        notify_scene=True,
                    )
            except Exception:
                payload = None
        if not isinstance(payload, dict):
            return False
        if not isinstance(mapping, dict):
            mapping = dict(getattr(self, "_mgl_retarget_joint_map", None) or {})
            mapping[source] = target
        self._mgl_retarget_joint_map = mapping
        self._mgl_retarget_links_dirty = True
        try:
            self._mgl_retarget_refresh_link_item()
        except Exception:
            pass
        self._mgl_retarget_notify_mapping_changed()
        try:
            self._mgl_retarget_log(
                "renderer_set_pelvis_constraint_link",
                source=source,
                target=target,
                mode=str(payload.get("mode") or ""),
            )
        except Exception:
            pass
        return True

    def _mgl_retarget_remove_joint_link(self, source_joint: str, target_joint: str = "") -> bool:
        source = str(source_joint or "").strip()
        target = str(target_joint or "").strip()
        if not source:
            return False
        node_item = getattr(self, "_mgl_retarget_node_item", None)
        mapping = None
        if node_item is not None:
            try:
                from nodes.anim_retarget import spec as anim_retarget_spec  # type: ignore

                remove_link = getattr(anim_retarget_spec, "remove_joint_map_link", None)
                if callable(remove_link):
                    mapping = remove_link(
                        node_item,
                        source,
                        target,
                        notify_scene=False,
                    )
            except Exception:
                mapping = None
        if not isinstance(mapping, dict):
            mapping = dict(getattr(self, "_mgl_retarget_joint_map", None) or {})
            current_target = str(mapping.get(source) or "").strip()
            if source not in mapping or (target and current_target != target):
                return False
            mapping.pop(source, None)
        self._mgl_retarget_joint_map = mapping
        self._mgl_retarget_links_dirty = True
        try:
            self._mgl_retarget_refresh_link_item()
        except Exception:
            pass
        self._mgl_retarget_notify_mapping_changed()
        try:
            self.update()
        except Exception:
            pass
        return True

    def _mgl_retarget_pick_ray(self, px: int, py: int, viewport_w: int, viewport_h: int):
        if np is None:
            return None
        P = getattr(self, "_mgl_pick_proj", None)
        V = getattr(self, "_mgl_pick_view", None)
        M = getattr(self, "_mgl_pick_model", None)
        if P is None or V is None or M is None:
            return None
        try:
            inv_pv = np.linalg.inv((P @ V @ M).astype(np.float32))
            ndc_fn = getattr(self, "_mgl_screen_to_active_ndc", None)
            ndc = ndc_fn(px, py, viewport_w, viewport_h) if callable(ndc_fn) else None
            if ndc is None:
                return None
            x, y = ndc
            near = np.array([x, y, -1.0, 1.0], dtype=np.float32)
            far = np.array([x, y, 1.0, 1.0], dtype=np.float32)
            p0 = inv_pv @ near
            p1 = inv_pv @ far
            if abs(float(p0[3])) < 1.0e-8 or abs(float(p1[3])) < 1.0e-8:
                return None
            p0 = p0[:3] / p0[3]
            p1 = p1[:3] / p1[3]
            ray_o = p0.astype(np.float32)
            ray_d = (p1 - p0).astype(np.float32)
            norm = float(np.linalg.norm(ray_d))
            if norm < 1.0e-8:
                return None
            ray_d /= norm
            return ray_o, ray_d
        except Exception:
            return None

    def _mgl_retarget_owner_model_for_pick(self, owner: str):
        if np is None:
            return None, 1.0
        owner_key = str(owner or "").strip().lower()
        if not owner_key:
            return None, 1.0
        scene = getattr(self, "_mgl_scene", None)
        model = None
        if scene is not None:
            try:
                for tag in ("retarget-handles", "scene-skeleton-handles"):
                    for item in scene.iter_by_tag(tag):
                        payload = getattr(item, "payload", None) or {}
                        item_owner = str(payload.get("owner") or "").strip().lower()
                        if item_owner != owner_key:
                            continue
                        raw_model = payload.get("model")
                        if raw_model is not None:
                            model = np.asarray(raw_model, dtype=np.float32).reshape(4, 4)
                        break
                    if model is not None:
                        break
            except Exception:
                model = None
        scale = 1.0
        if model is not None:
            try:
                sx = float(np.linalg.norm(model[0, :3]))
                sy = float(np.linalg.norm(model[1, :3]))
                sz = float(np.linalg.norm(model[2, :3]))
                scale = max(1.0e-6, sx, sy, sz)
            except Exception:
                scale = 1.0
        return model, scale

    def _mgl_retarget_pick_positions_for_owner(self, owner: str, handles: list):
        if np is None:
            return np.zeros((0, 3), dtype=np.float32), 1.0
        rows = []
        for handle in list(handles or []):
            try:
                rows.append(handle.get("position", (0.0, 0.0, 0.0)))
            except Exception:
                rows.append((0.0, 0.0, 0.0))
        try:
            positions = np.asarray(rows, dtype=np.float32).reshape(-1, 3)
        except Exception:
            positions = np.zeros((0, 3), dtype=np.float32)
        try:
            if any(
                isinstance(handle, dict) and str(handle.get("role") or "").strip().lower() == "scene_skeleton"
                for handle in list(handles or [])
            ):
                return positions, 1.0
        except Exception:
            pass
        model, radius_scale = self._mgl_retarget_owner_model_for_pick(owner)
        if model is None or positions.size == 0:
            return positions, radius_scale
        try:
            ones = np.ones((positions.shape[0], 1), dtype=np.float32)
            p4 = np.concatenate([positions, ones], axis=1) @ model
            w = p4[:, 3:4]
            safe = np.abs(w) > 1.0e-8
            out = p4[:, :3].astype(np.float32, copy=True)
            out[safe[:, 0]] = out[safe[:, 0]] / w[safe[:, 0]]
            return out, radius_scale
        except Exception:
            return positions, radius_scale

    def pick_retarget_joint_at(self, px: int, py: int, viewport_w: int, viewport_h: int, role: Optional[str] = None):
        scene_handles_by_owner = getattr(self, "_mgl_scene_skeleton_handles_by_owner", None)
        active_scene_owner = str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "").strip().lower()
        if isinstance(scene_handles_by_owner, dict):
            if active_scene_owner:
                scene_handles_by_owner = {
                    key: value
                    for key, value in scene_handles_by_owner.items()
                    if str(key or "").strip().lower() == active_scene_owner
                }
            else:
                scene_handles_by_owner = {}
        has_scene_handles = isinstance(scene_handles_by_owner, dict) and bool(scene_handles_by_owner)
        if np is None or (not bool(getattr(self, "_mgl_retarget_preview_active", False)) and not has_scene_handles):
            return None
        ray = self._mgl_retarget_pick_ray(px, py, viewport_w, viewport_h)
        if ray is None:
            return None
        ray_o, ray_d = ray
        role_filter = str(role or "").strip().lower()
        handles_by_owner = getattr(self, "_mgl_retarget_joint_handles_by_owner", None)
        merged_handles_by_owner = {}
        if isinstance(handles_by_owner, dict):
            merged_handles_by_owner.update(handles_by_owner)
        if isinstance(scene_handles_by_owner, dict):
            merged_handles_by_owner.update(scene_handles_by_owner)
        if not merged_handles_by_owner:
            return None

        best = None
        best_dist2 = 1.0e30
        best_t = 1.0e30
        for owner, handles in merged_handles_by_owner.items():
            if not isinstance(handles, list) or not handles:
                continue
            positions, radius_scale = self._mgl_retarget_pick_positions_for_owner(str(owner), handles)
            for index, handle in enumerate(handles):
                handle_role = str((handle or {}).get("role") or "").strip().lower()
                if role_filter and handle_role != role_filter:
                    continue
                if index >= int(positions.shape[0]):
                    continue
                pos = positions[index]
                v = pos - ray_o
                t = float(v @ ray_d)
                if t < 0.0:
                    continue
                closest = ray_o + (t * ray_d)
                delta = pos - closest
                dist2 = float(delta @ delta)
                try:
                    radius = float((handle or {}).get("pick_radius", 0.12) or 0.12)
                except Exception:
                    radius = 0.12
                radius *= float(radius_scale)
                if dist2 > radius * radius:
                    continue
                if dist2 < best_dist2 or (abs(dist2 - best_dist2) < 1.0e-8 and t < best_t):
                    best_dist2 = dist2
                    best_t = t
                    best = dict(handle or {})
                    best["owner"] = str(owner)
                    best["position"] = (float(pos[0]), float(pos[1]), float(pos[2]))
        return best

    def _mgl_disable_splats(self) -> None:
        try:
            self._mgl_log("splats: disable")
        except Exception:
            pass
        self._mgl_pending_splats = None
        self._mgl_pending_splat_lit_flags = None
        self._mgl_pending_splat_glow_flags = None
        self._mgl_render_splats = False
        self._mgl_splat_count = 0
        self._mgl_splats15_cpu = None
        self._mgl_splat_lit_cpu = None
        self._mgl_splat_glow_cpu = None
        self._mgl_splats_all_lit = False
        self._mgl_splats_has_lit = False
        try:
            # keep dicts stable so picking/xforms don't fall back to mesh state
            if not isinstance(getattr(self, "_mgl_scene_splats_world", None), dict):
                self._mgl_scene_splats_world = {}
            else:
                self._mgl_scene_splats_world.clear()
        except Exception:
            pass
        try:
            if not isinstance(getattr(self, "_mgl_scene_splat_glow_by_owner", None), dict):
                self._mgl_scene_splat_glow_by_owner = {}
            else:
                self._mgl_scene_splat_glow_by_owner.clear()
        except Exception:
            pass
        try:
            if not isinstance(getattr(self, "_mgl_scene_splats_bounds_local", None), dict):
                self._mgl_scene_splats_bounds_local = {}
        except Exception:
            pass
        try:
            if not isinstance(getattr(self, "_mgl_scene_splat_xforms_by_owner", None), dict):
                self._mgl_scene_splat_xforms_by_owner = {}
        except Exception:
            pass
        try:
            if not isinstance(getattr(self, "_mgl_scene_splat_bounds_by_owner", None), dict):
                self._mgl_scene_splat_bounds_by_owner = {}
        except Exception:
            pass

    def get_scene_owner_bounds(self, owner: str):
        key = str(owner or "").strip()
        if not key:
            if np is None:
                return (None, None)
            z = np.array([0.0, 0.0, 0.0], dtype=np.float32)
            return (z, z)

        # splat bounds (world)
        try:
            splat_bounds = getattr(self, "_mgl_scene_splat_bounds_by_owner", None)
            if isinstance(splat_bounds, dict):
                if key in splat_bounds:
                    return splat_bounds[key]
                lk = key.lower()
                for k, v in splat_bounds.items():
                    try:
                        if str(k).strip().lower() == lk:
                            return v
                    except Exception:
                        continue
        except Exception:
            pass

        # mesh bounds
        try:
            mesh_bounds = getattr(self, "_mgl_scene_mesh_bounds_by_owner", None)
            if not isinstance(mesh_bounds, dict):
                mesh_bounds = getattr(self, "_mgl_scene_bounds_by_owner", None)
            if isinstance(mesh_bounds, dict):
                match_owner = None
                bounds = None
                if key in mesh_bounds:
                    match_owner = key
                    bounds = mesh_bounds[key]
                else:
                    lk = key.lower()
                    for k, v in mesh_bounds.items():
                        try:
                            if str(k).strip().lower() == lk:
                                match_owner = k
                                bounds = v
                                break
                        except Exception:
                            continue

                if bounds is None:
                    pass
                elif np is None:
                    return bounds
                else:
                    try:
                        bmin, bmax = bounds
                        bmin = np.array(bmin, dtype=np.float32)
                        bmax = np.array(bmax, dtype=np.float32)
                        if bmin.shape[0] < 3 or bmax.shape[0] < 3:
                            return bounds
                    except Exception:
                        return bounds

                    try:
                        xf_owner = match_owner if match_owner is not None else owner
                        model = None
                        scene = getattr(self, "_mgl_scene", None)
                        if scene is not None:
                            try:
                                for item in scene.iter_by_tag("scene-model"):
                                    payload = getattr(item, "payload", None) or {}
                                    o = payload.get("owner")
                                    if o == xf_owner:
                                        model = payload.get("model")
                                    elif o is not None:
                                        try:
                                            if str(o).strip().lower() == str(xf_owner).strip().lower():
                                                model = payload.get("model")
                                        except Exception:
                                            pass
                                    if model is not None:
                                        break
                            except Exception:
                                model = None

                        if model is None:
                            # Fallback to owner xform when no scene item model exists.
                            try:
                                xf = None
                                try:
                                    xf = self._mgl_get_scene_asset_xform(xf_owner)
                                except Exception:
                                    xf = None
                                if isinstance(xf, dict):
                                    px, py, pz = xf.get("pos", (0.0, 0.0, 0.0))
                                    rx, ry, rz = xf.get("rot", (0.0, 0.0, 0.0))
                                    sx, sy, sz = xf.get("scl", (1.0, 1.0, 1.0))
                                    cx, cy, cz = self._mgl_owner_pivot_local(xf_owner, bmin, bmax)

                                    # Match _mgl_set_scene_asset_xform rotation convention for scene meshes.
                                    try:
                                        rot_rx = -float(rx)
                                        rot_ry = -float(ry)
                                        rot_rz = -float(rz)
                                    except Exception:
                                        rot_rx, rot_ry, rot_rz = rx, ry, rz

                                    def T(tx, ty, tz):
                                        m = np.eye(4, dtype=np.float32)
                                        m[3, 0] = tx
                                        m[3, 1] = ty
                                        m[3, 2] = tz
                                        return m

                                    def S(sx, sy, sz):
                                        m = np.eye(4, dtype=np.float32)
                                        m[0, 0] = sx
                                        m[1, 1] = sy
                                        m[2, 2] = sz
                                        return m

                                    def Rx(a):
                                        a = math.radians(a)
                                        c, s = math.cos(a), math.sin(a)
                                        m = np.eye(4, dtype=np.float32)
                                        m[1, 1] = c
                                        m[1, 2] = s
                                        m[2, 1] = -s
                                        m[2, 2] = c
                                        return m

                                    def Ry(a):
                                        a = math.radians(a)
                                        c, s = math.cos(a), math.sin(a)
                                        m = np.eye(4, dtype=np.float32)
                                        m[0, 0] = c
                                        m[0, 2] = -s
                                        m[2, 0] = s
                                        m[2, 2] = c
                                        return m

                                    def Rz(a):
                                        a = math.radians(a)
                                        c, s = math.cos(a), math.sin(a)
                                        m = np.eye(4, dtype=np.float32)
                                        m[0, 0] = c
                                        m[0, 1] = s
                                        m[1, 0] = -s
                                        m[1, 1] = c
                                        return m

                                    R = (Rx(rot_rx) @ Ry(rot_ry) @ Rz(rot_rz))
                                    xform_space = str(getattr(self, "_mgl_xform_space", "world") or "world").lower()
                                    if xform_space == "local":
                                        model = T(-cx, -cy, -cz) @ S(sx, sy, sz) @ R @ T(px, py, pz)
                                    else:
                                        model = T(-cx, -cy, -cz) @ R @ S(sx, sy, sz) @ T(px, py, pz)
                            except Exception:
                                model = None
                            if model is None:
                                return bounds

                        try:
                            if Matrix44 is not None and isinstance(model, Matrix44):
                                model = np.array(model, dtype=np.float32)
                            else:
                                model = np.array(model, dtype=np.float32)
                        except Exception:
                            return bounds

                        if getattr(model, "shape", None) != (4, 4):
                            return bounds

                        corners = np.array(
                            [
                                [bmin[0], bmin[1], bmin[2]],
                                [bmin[0], bmin[1], bmax[2]],
                                [bmin[0], bmax[1], bmin[2]],
                                [bmin[0], bmax[1], bmax[2]],
                                [bmax[0], bmin[1], bmin[2]],
                                [bmax[0], bmin[1], bmax[2]],
                                [bmax[0], bmax[1], bmin[2]],
                                [bmax[0], bmax[1], bmax[2]],
                            ],
                            dtype=np.float32,
                        )
                        corners_h = np.concatenate(
                            [corners, np.ones((corners.shape[0], 1), dtype=np.float32)],
                            axis=1,
                        )
                        world = corners_h @ model
                        w = world[:, 3]
                        world_xyz = world[:, :3].copy()
                        mask = np.abs(w) > 1e-8
                        if np.any(mask):
                            world_xyz[mask] = world_xyz[mask] / w[mask, None]
                        mins = world_xyz.min(axis=0).astype(np.float32)
                        maxs = world_xyz.max(axis=0).astype(np.float32)
                        return (mins, maxs)
                    except Exception:
                        return bounds
        except Exception:
            pass

        if np is None:
            return (None, None)
        z = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        return (z, z)

    def _mgl_set_scene_item_visibility(self, key: str, visible: bool) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None or not key:
            return
        key_norm = str(key).strip().lower()
        for item in scene.items():
            payload = item.payload or {}
            owner = payload.get("owner") or payload.get("node")
            path_key = payload.get("path")
            target_owner = payload.get("target_owner")
            owner_norm = str(owner).strip().lower() if owner is not None else ""
            path_norm = str(path_key).strip().lower() if path_key is not None else ""
            target_norm = str(target_owner).strip().lower() if target_owner is not None else ""
            if owner == key or path_key == key or owner_norm == key_norm or path_norm == key_norm or target_norm == key_norm:
                if item.tag == "scene-wire":
                    item.visible = bool(visible) and bool(getattr(self, "_mgl_wireframe", False))
                elif item.tag in {"scene-model", "model"} and bool(payload.get("fbx_bind_joints_only", False)):
                    item.visible = False
                    self._mgl_fbx_joints_log(
                        "visibility enforce_hidden "
                        + f"key={key} tag={item.tag} owner={owner or ''} path={path_key or ''}"
                    )
                else:
                    item.visible = visible
                if item.tag == "scene-rig-joints":
                    self._mgl_fbx_joints_log(
                        "visibility set "
                        + f"key={key} tag={item.tag} owner={owner or ''} path={path_key or ''} "
                        + f"visible={bool(item.visible)} requested={bool(visible)}"
                    )

    def _mgl_rename_scene_item_owner(self, old_name: str, new_name: str) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None or not old_name or not new_name or old_name == new_name:
            return
        old_norm = str(old_name).strip().lower()
        for item in scene.items():
            payload = item.payload or {}
            owner = payload.get("owner") or payload.get("node")
            if owner == old_name:
                payload["owner"] = new_name
            elif owner is not None and str(owner).strip().lower() == old_norm:
                payload["owner"] = new_name
            target_owner = payload.get("target_owner")
            if target_owner == old_name:
                payload["target_owner"] = new_name
            elif target_owner is not None and str(target_owner).strip().lower() == old_norm:
                payload["target_owner"] = new_name

    def _mgl_fx_color_rgba(self, raw_color):
        if isinstance(raw_color, (list, tuple)):
            vals = [float(v) for v in raw_color[:4]]
            if len(vals) >= 3:
                if len(vals) < 4:
                    vals.append(1.0)
                return tuple(max(0.0, min(1.0, float(v))) for v in vals[:4])
        text = str(raw_color or "").strip()
        if not text:
            text = "#b7ff6a"
        if not text.startswith("#"):
            text = f"#{text}"
        if len(text) == 4:
            text = "#" + "".join(ch * 2 for ch in text[1:])
        if len(text) != 7:
            text = "#b7ff6a"
        try:
            rgb = int(text[1:], 16)
        except Exception:
            rgb = int("b7ff6a", 16)
        return (
            float((rgb >> 16) & 255) / 255.0,
            float((rgb >> 8) & 255) / 255.0,
            float(rgb & 255) / 255.0,
            0.95,
        )

    def _mgl_fx_curve_points(self, payload, key: str, fallback: list[tuple[float, float]]) -> list[tuple[float, float]]:
        rows: list[tuple[float, float]] = []
        raw_points = []
        try:
            raw_points = payload.get(key) or []
        except Exception:
            raw_points = []
        for raw in raw_points:
            x_val = None
            y_val = None
            if isinstance(raw, dict):
                x_val = raw.get("x")
                y_val = raw.get("y")
            elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
                x_val = raw[0]
                y_val = raw[1]
            try:
                x = max(0.0, min(1.0, float(x_val)))
                y = max(0.0, min(1.0, float(y_val)))
            except Exception:
                continue
            rows.append((x, y))
        if len(rows) < 2:
            rows = list(fallback)
        rows.sort(key=lambda item: (float(item[0]), float(item[1])))
        merged: list[tuple[float, float]] = []
        for x, y in rows:
            if merged and abs(float(merged[-1][0]) - float(x)) < 1.0e-5:
                merged[-1] = (float(x), float(y))
            else:
                merged.append((float(x), float(y)))
        if not merged:
            merged = list(fallback)
        if merged[0][0] > 0.0:
            merged.insert(0, (0.0, float(merged[0][1])))
        else:
            merged[0] = (0.0, float(merged[0][1]))
        if merged[-1][0] < 1.0:
            merged.append((1.0, float(merged[-1][1])))
        else:
            merged[-1] = (1.0, float(merged[-1][1]))
        return merged

    def _mgl_fx_profile_points(self, payload) -> list[tuple[float, float]]:
        return self._mgl_fx_curve_points(
            payload,
            "profile_points",
            [(0.0, 0.2), (0.25, 0.95), (0.5, 0.2), (0.75, 0.95), (1.0, 0.2)],
        )

    def _mgl_fx_age_scale_points(self, payload) -> list[tuple[float, float]]:
        return self._mgl_fx_curve_points(payload, "age_scale_points", [(0.0, 0.25), (1.0, 1.0)])

    def _mgl_fx_curve_value(self, points: list[tuple[float, float]], t: float, default: float) -> float:
        tt = max(0.0, min(1.0, float(t)))
        if not points:
            return float(default)
        if tt <= float(points[0][0]):
            return float(points[0][1])
        for idx in range(1, len(points)):
            x0, y0 = points[idx - 1]
            x1, y1 = points[idx]
            if tt <= float(x1):
                span = float(x1) - float(x0)
                if span <= 1.0e-6:
                    return float(y1)
                alpha = (tt - float(x0)) / span
                return float(y0) + (float(y1) - float(y0)) * float(alpha)
        return float(points[-1][1])

    def _mgl_fx_profile_value(self, payload, t: float) -> float:
        return self._mgl_fx_curve_value(self._mgl_fx_profile_points(payload), t, 0.2)

    def _mgl_fx_age_t(self, age: int, lifespan: int) -> float:
        life_frames = max(1, int(lifespan))
        if life_frames <= 1:
            return 0.0
        return max(0.0, min(1.0, float(age) / float(max(1, life_frames - 1))))

    def _mgl_fx_substeps(self, payload) -> int:
        try:
            return max(1, int(payload.get("substeps", 1)))
        except Exception:
            return 1

    def _mgl_fx_spawn_rate(self, payload) -> float:
        try:
            return max(0.01, float(payload.get("spawn_rate", payload.get("frame_step", 1.0))))
        except Exception:
            return 1.0

    def _mgl_fx_spawn_interval(self, payload) -> float:
        substeps = max(1, self._mgl_fx_substeps(payload))
        quantum = 1.0 / float(substeps)
        return max(float(self._mgl_fx_spawn_rate(payload)), quantum)

    def _mgl_fx_profile_phase(self, payload, emit_index: int, spawn_interval: float, lifespan: int) -> float:
        try:
            repeats = max(1, int(payload.get("repeats", 1)))
        except Exception:
            repeats = 1
        try:
            life_steps = max(1, int(math.ceil(float(max(1, lifespan)) / float(max(0.01, spawn_interval)))))
        except Exception:
            life_steps = 1
        if life_steps <= 1:
            return 0.0
        phase = math.fmod((float(max(0, int(emit_index))) / float(life_steps)) * float(repeats), 1.0)
        if phase < 0.0:
            phase += 1.0
        return float(phase)

    def _mgl_fx_age_scale_value(self, payload, age: int, lifespan: int) -> float:
        age_t = self._mgl_fx_age_t(age, lifespan)
        ramp_t = self._mgl_fx_curve_value(self._mgl_fx_age_scale_points(payload), age_t, 1.0)
        try:
            scale_min = float(payload.get("age_scale_min", 1.0))
        except Exception:
            scale_min = 1.0
        try:
            scale_max = float(payload.get("age_scale_max", 1.0))
        except Exception:
            scale_max = 1.0
        ramp_t = max(0.0, min(1.0, float(ramp_t)))
        if abs(float(scale_max) - float(scale_min)) <= 1.0e-6:
            return float(ramp_t)
        return float(scale_min) + ((float(scale_max) - float(scale_min)) * float(ramp_t))

    def _mgl_fx_owner_candidates(self, payload) -> list[str]:
        out = []
        seen = set()
        raw_values = [payload.get("target_owner")]
        aliases = payload.get("target_owner_aliases")
        if isinstance(aliases, (list, tuple)):
            raw_values.extend(list(aliases))
        for raw in raw_values:
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    def _mgl_fx_pick_owner(self, payload) -> str:
        candidates = self._mgl_fx_owner_candidates(payload)
        if not candidates:
            return ""
        best_owner = candidates[0]
        best_score = -1
        bounds_getter = getattr(self, "get_scene_owner_bounds", None)
        for owner in candidates:
            score = 0
            try:
                if self._mgl_fx_current_owner_pos(owner) is not None:
                    score += 4
            except Exception:
                pass
            try:
                keys_map = self._mgl_timeline_owner_keys_map(owner)
                if isinstance(keys_map, dict) and keys_map:
                    score += 2
            except Exception:
                pass
            try:
                if callable(bounds_getter):
                    bmin, bmax = bounds_getter(owner)
                    if bmin is not None and bmax is not None:
                        score += 1
            except Exception:
                pass
            if score > best_score:
                best_score = score
                best_owner = owner
        return best_owner

    def _mgl_fx_sample_entries(self, payload, frame: int, active_owner: str):
        if np is None:
            return []
        try:
            lifespan = max(1.0, float(payload.get("lifespan", 28)))
        except Exception:
            lifespan = 28.0
        substeps = max(1, self._mgl_fx_substeps(payload))
        spawn_interval = max(0.01, float(self._mgl_fx_spawn_interval(payload)))
        sample_limit = max(1, int(math.ceil(float(lifespan) / float(spawn_interval))))
        entries = []
        for idx in range(int(sample_limit)):
            sample_age = float(idx) * float(spawn_interval)
            if sample_age >= float(lifespan):
                break
            sample_frame = float(frame) - float(sample_age)
            if sample_frame < 0.0:
                break
            sample_frame_q = round(float(sample_frame) * float(substeps)) / float(substeps)
            pos = self._mgl_fx_eval_owner_pos(active_owner, sample_frame_q)
            if pos is None:
                if idx == 0:
                    continue
                break
            vec = np.array(pos, dtype=np.float32)
            if entries:
                try:
                    if float(np.linalg.norm(vec - entries[-1]["center"])) < 1.0e-5:
                        continue
                except Exception:
                    pass
            entries.append(
                {
                    "frame": float(sample_frame_q),
                    "age": float(sample_age),
                    "emit_index": int(max(0, round(float(sample_frame_q) / float(spawn_interval)))),
                    "center": vec,
                }
            )
        return entries

    def _mgl_fx_ring_axes_from_tangent(self, tangent):
        if np is None:
            return None, None
        last_tangent = np.array([0.0, 0.0, 1.0], dtype=np.float32)
        try:
            tlen = float(np.linalg.norm(tangent))
        except Exception:
            tlen = 0.0
        if tlen > 1.0e-6:
            normal = np.asarray(tangent, dtype=np.float32) / tlen
            last_tangent = normal.astype(np.float32)
        else:
            normal = last_tangent
        ref = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        try:
            if abs(float(np.dot(normal, ref))) > 0.92:
                ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        except Exception:
            ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        axis_u = np.cross(ref, normal)
        try:
            ulen = float(np.linalg.norm(axis_u))
        except Exception:
            ulen = 0.0
        if ulen <= 1.0e-6:
            axis_u = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            ulen = 1.0
        axis_u = axis_u / float(ulen)
        axis_v = np.cross(normal, axis_u)
        try:
            vlen = float(np.linalg.norm(axis_v))
        except Exception:
            vlen = 0.0
        if vlen <= 1.0e-6:
            axis_v = np.array([0.0, 1.0, 0.0], dtype=np.float32)
            vlen = 1.0
        axis_v = axis_v / float(vlen)
        return axis_u, axis_v

    def _mgl_fx_tangent_for_sample(self, payload, active_owner: str, sample_frame: float, prev_center, next_center):
        sample_step = float(self._mgl_fx_spawn_interval(payload))
        center = self._mgl_fx_eval_owner_pos(active_owner, float(sample_frame))
        if center is None:
            center_vec = None
        else:
            center_vec = np.array(center, dtype=np.float32)
        before = self._mgl_fx_eval_owner_pos(active_owner, float(sample_frame) - float(sample_step))
        after = self._mgl_fx_eval_owner_pos(active_owner, float(sample_frame) + float(sample_step))
        before_vec = np.array(before, dtype=np.float32) if before is not None and np is not None else None
        after_vec = np.array(after, dtype=np.float32) if after is not None and np is not None else None
        if before_vec is not None and after_vec is not None:
            return after_vec - before_vec
        if center_vec is not None and after_vec is not None:
            return after_vec - center_vec
        if center_vec is not None and before_vec is not None:
            return center_vec - before_vec
        if prev_center is not None and next_center is not None:
            return np.asarray(next_center, dtype=np.float32) - np.asarray(prev_center, dtype=np.float32)
        if next_center is not None and center_vec is not None:
            return np.asarray(next_center, dtype=np.float32) - center_vec
        if prev_center is not None and center_vec is not None:
            return center_vec - np.asarray(prev_center, dtype=np.float32)
        return np.array([0.0, 0.0, 1.0], dtype=np.float32)

    def _mgl_timeline_owner_keys_map(self, owner: str):
        key = str(owner or "").strip()
        if not key:
            return {}
        try:
            decoded = self._mgl_scene_skeleton_decode_joint_owner(key)
        except Exception:
            decoded = None
        norm_fn = getattr(self, "_timeline_owner_norm", None)
        key_norm = norm_fn(key) if callable(norm_fn) else key.lower()
        current_owner = str(getattr(self, "_timeline_owner_name", "") or "").strip()
        current_norm = norm_fn(current_owner) if callable(norm_fn) else current_owner.lower()
        current_keys = getattr(self, "_timeline_keys", None)
        if (
            key_norm
            and current_norm == key_norm
            and isinstance(current_keys, dict)
            and bool(current_keys)
        ):
            return current_keys
        if decoded:
            return self._mgl_scene_skeleton_joint_keys_map(key)

        owner_paths_fn = getattr(self, "_timeline_owner_file_paths", None)
        reader = getattr(self, "_timeline_read_owner_keys_file", None)
        file_paths = []
        if callable(owner_paths_fn):
            try:
                file_paths = owner_paths_fn()
            except Exception:
                file_paths = []
        cache = getattr(self, "_timeline_owner_keys_cache", None)
        if not isinstance(cache, dict):
            cache = {}
        if callable(reader):
            for path in file_paths or []:
                path_key = str(path)
                try:
                    mtime = float(path.stat().st_mtime)
                except Exception:
                    mtime = None
                entry = cache.get(path_key) if isinstance(cache, dict) else None
                owner_name = ""
                keys_map = {}
                if isinstance(entry, dict) and entry.get("mtime", None) == mtime:
                    owner_name = str(entry.get("owner", "") or "").strip()
                    cached_keys = entry.get("keys", None)
                    if isinstance(cached_keys, dict):
                        keys_map = cached_keys
                else:
                    owner_name, keys_map = reader(path)
                    cache[path_key] = {
                        "mtime": mtime,
                        "owner": str(owner_name or ""),
                        "keys": keys_map if isinstance(keys_map, dict) else {},
                    }
                owner_norm = norm_fn(owner_name) if callable(norm_fn) else str(owner_name or "").strip().lower()
                if owner_norm and owner_norm == key_norm and isinstance(keys_map, dict):
                    self._timeline_owner_keys_cache = cache
                    return keys_map
        self._timeline_owner_keys_cache = cache
        context = self._mgl_scene_owner_fbx_rig_context(key)
        if isinstance(context, dict):
            keys_map = self._mgl_fbx_clip_marker_keys_map(key, context)
            if isinstance(keys_map, dict):
                return keys_map
        return {}

    def _mgl_fx_current_owner_pos(self, owner: str):
        key = str(owner or "").strip()
        if not key:
            return None
        xf = None
        get_xf = getattr(self, "_timeline_get_owner_xform", None)
        if callable(get_xf):
            try:
                xf, _is_splat = get_xf(key)
            except Exception:
                xf = None
        if not isinstance(xf, dict):
            try:
                xf = self._mgl_get_scene_asset_xform(key)
            except Exception:
                xf = None
        if not isinstance(xf, dict):
            return None
        pos = xf.get("pos", None)
        if not isinstance(pos, (list, tuple)) or len(pos) < 3:
            return None
        try:
            return (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            return None

    def _mgl_fx_eval_owner_pos_int(self, owner: str, frame: int):
        key = str(owner or "").strip()
        if not key:
            return None
        cur_frame_fn = getattr(self, "_timeline_current_frame", None)
        cur_frame = None
        if callable(cur_frame_fn):
            try:
                cur_frame = int(cur_frame_fn())
            except Exception:
                cur_frame = None
        keys_map = self._mgl_timeline_owner_keys_map(key)
        if isinstance(keys_map, dict) and keys_map:
            eval_fn = getattr(self, "_timeline_eval_frame_values_for_owner_keys", None)
            if callable(eval_fn):
                try:
                    xyz_eval, _rxyz_eval = eval_fn(key, keys_map, int(frame))
                    if isinstance(xyz_eval, (list, tuple)) and len(xyz_eval) >= 3:
                        return (float(xyz_eval[0]), float(xyz_eval[1]), float(xyz_eval[2]))
                except Exception:
                    pass
            try:
                entry = keys_map.get(int(frame))
            except Exception:
                entry = None
            if isinstance(entry, dict):
                xyz = entry.get("xyz", None)
                if isinstance(xyz, (list, tuple)) and len(xyz) >= 3:
                    try:
                        return (float(xyz[0]), float(xyz[1]), float(xyz[2]))
                    except Exception:
                        pass
        live_pos = self._mgl_fx_current_owner_pos(key)
        if cur_frame is not None and int(frame) == int(cur_frame) and live_pos is not None:
            return live_pos
        return None

    def _mgl_fx_eval_owner_pos(self, owner: str, frame):
        key = str(owner or "").strip()
        if not key:
            return None
        try:
            frame_f = float(frame)
        except Exception:
            return None
        frame_i = int(round(frame_f))
        if abs(frame_f - float(frame_i)) <= 1.0e-5:
            return self._mgl_fx_eval_owner_pos_int(key, frame_i)
        lo = int(math.floor(frame_f))
        hi = int(math.ceil(frame_f))
        if hi == lo:
            return self._mgl_fx_eval_owner_pos_int(key, lo)
        pos_lo = self._mgl_fx_eval_owner_pos_int(key, lo)
        pos_hi = self._mgl_fx_eval_owner_pos_int(key, hi)
        if pos_lo is None and pos_hi is None:
            return None
        if pos_lo is None:
            return pos_hi
        if pos_hi is None:
            return pos_lo
        alpha = max(0.0, min(1.0, float(frame_f - float(lo))))
        try:
            return (
                (float(pos_lo[0]) * (1.0 - alpha)) + (float(pos_hi[0]) * alpha),
                (float(pos_lo[1]) * (1.0 - alpha)) + (float(pos_hi[1]) * alpha),
                (float(pos_lo[2]) * (1.0 - alpha)) + (float(pos_hi[2]) * alpha),
            )
        except Exception:
            return pos_lo

    def _mgl_fx_build_trail_line_points(self, payload, frame: int):
        if np is None:
            self._mgl_fx_log_throttled("numpy-missing", "[renderer] build skip: numpy unavailable", interval=3.0)
            return None
        target_owner = str(payload.get("target_owner") or "").strip()
        active_owner = self._mgl_fx_pick_owner(payload) or target_owner
        payload["_fx_active_owner"] = active_owner
        if not active_owner:
            owner = str(payload.get("owner") or "").strip()
            self._mgl_fx_log_throttled(
                f"missing-target:{owner or 'unknown'}",
                f"[renderer] build skip: missing target owner owner={owner!r}",
                interval=1.0,
            )
            return None
        try:
            enabled = bool(payload.get("enabled", True))
        except Exception:
            enabled = True
        if not enabled:
            self._mgl_fx_log_throttled(
                f"disabled:{target_owner}",
                f"[renderer] build skip: disabled target_owner={target_owner} frame={int(frame)}",
                interval=1.0,
            )
            return None
        try:
            samples = max(1, int(payload.get("samples", 28)))
        except Exception:
            samples = 28
        try:
            lifespan = max(1, int(payload.get("lifespan", int(samples))))
        except Exception:
            lifespan = max(1, int(samples))
        spawn_rate = float(self._mgl_fx_spawn_rate(payload))
        substeps = int(self._mgl_fx_substeps(payload))
        spawn_interval = float(self._mgl_fx_spawn_interval(payload))
        try:
            base_radius = max(0.001, float(payload.get("radius", 0.35)))
        except Exception:
            base_radius = 0.35
        try:
            global_space = bool(payload.get("global_space", False))
        except Exception:
            global_space = False
        try:
            bounds_getter = getattr(self, "get_scene_owner_bounds", None)
            if callable(bounds_getter):
                bmin, bmax = bounds_getter(active_owner)
                if bmin is not None and bmax is not None:
                    extents = np.asarray(bmax, dtype=np.float32) - np.asarray(bmin, dtype=np.float32)
                    owner_scale = max(0.0, float(np.max(np.abs(extents))) * 0.5)
                    if owner_scale > 1.0:
                        base_radius = max(float(base_radius), float(base_radius) * float(owner_scale))
        except Exception:
            pass
        try:
            sides = max(6, int(payload.get("sides", 28)))
        except Exception:
            sides = 28
        try:
            repeats = max(1, int(payload.get("repeats", 1)))
        except Exception:
            repeats = 1
        global_space = bool(payload.get("global_space", False))

        entries = self._mgl_fx_sample_entries(payload, frame, active_owner)
        if not entries:
            payload["_fx_debug_oldest_center"] = None
            payload["_fx_debug_newest_center"] = None
            payload["_fx_debug_oldest_frame"] = None
            payload["_fx_debug_newest_frame"] = None
            self._mgl_fx_log(
                f"[renderer] build no-centers target_owner={target_owner} active_owner={active_owner} "
                f"frame={int(frame)} spawn_rate={spawn_rate:.3f} substeps={int(substeps)} interval={spawn_interval:.3f} "
                f"samples={int(samples)} lifespan={int(lifespan)} "
                f"repeats={int(repeats)} global={global_space} "
                f"aliases={self._mgl_fx_owner_candidates(payload)!r}"
            )
            return None
        entries = list(reversed(entries))
        try:
            oldest_entry = entries[0]
            newest_entry = entries[-1]
            payload["_fx_debug_oldest_center"] = tuple(float(v) for v in oldest_entry["center"])
            payload["_fx_debug_newest_center"] = tuple(float(v) for v in newest_entry["center"])
            payload["_fx_debug_oldest_frame"] = float(oldest_entry["frame"])
            payload["_fx_debug_newest_frame"] = float(newest_entry["frame"])
        except Exception:
            payload["_fx_debug_oldest_center"] = None
            payload["_fx_debug_newest_center"] = None
            payload["_fx_debug_oldest_frame"] = None
            payload["_fx_debug_newest_frame"] = None
        line_pos: List[float] = []
        count = len(entries)
        for idx, entry in enumerate(entries):
            center = entry["center"]
            prev_center = entries[idx - 1]["center"] if idx > 0 else None
            next_center = entries[idx + 1]["center"] if idx < (count - 1) else None
            if global_space:
                tangent = self._mgl_fx_tangent_for_sample(
                    payload,
                    active_owner,
                    float(entry["frame"]),
                    prev_center,
                    next_center,
                )
            else:
                if count == 1:
                    tangent = np.array([0.0, 0.0, 1.0], dtype=np.float32)
                elif idx == 0:
                    tangent = np.asarray(next_center, dtype=np.float32) - center if next_center is not None else np.array([0.0, 0.0, 1.0], dtype=np.float32)
                elif idx == (count - 1):
                    tangent = center - np.asarray(prev_center, dtype=np.float32) if prev_center is not None else np.array([0.0, 0.0, 1.0], dtype=np.float32)
                else:
                    tangent = np.asarray(next_center, dtype=np.float32) - np.asarray(prev_center, dtype=np.float32)
            axis_u, axis_v = self._mgl_fx_ring_axes_from_tangent(tangent)
            if axis_u is None or axis_v is None:
                continue
            phase_t = self._mgl_fx_profile_phase(
                payload,
                int(entry.get("emit_index", 0)),
                float(spawn_interval),
                int(lifespan),
            )
            profile_val = self._mgl_fx_profile_value(payload, phase_t)
            age_scale = max(0.0, float(self._mgl_fx_age_scale_value(payload, float(entry["age"]), int(lifespan))))
            ring_radius = float(base_radius) * max(0.08, float(profile_val)) * age_scale
            for seg in range(int(sides)):
                a0 = (2.0 * math.pi * float(seg)) / float(sides)
                a1 = (2.0 * math.pi * float(seg + 1)) / float(sides)
                p0 = center + (math.cos(a0) * ring_radius * axis_u) + (math.sin(a0) * ring_radius * axis_v)
                p1 = center + (math.cos(a1) * ring_radius * axis_u) + (math.sin(a1) * ring_radius * axis_v)
                line_pos.extend(
                    [
                        float(p0[0]), float(p0[1]), float(p0[2]),
                        float(p1[0]), float(p1[1]), float(p1[2]),
                    ]
                )
        if not line_pos:
            return None
        return np.array(line_pos, dtype="f4").reshape(-1, 3)

    def _mgl_fx_release_mesh_cache(self, item: MGLSceneItem, payload) -> None:
        mesh_cache = payload.pop("_fx_instance_mesh", None)
        payload.pop("_fx_instance_loaded_path", None)
        payload.pop("_fx_instance_models", None)
        tex_override = payload.pop("_fx_instance_texture", None)
        payload.pop("_fx_instance_texture_path", None)
        payload.pop("_fx_instance_texture_provider_id", None)
        seen = set()
        for res in list((mesh_cache or {}).get("resources") or []):
            if res is None:
                continue
            rid = id(res)
            if rid in seen:
                continue
            seen.add(rid)
            if hasattr(res, "release"):
                try:
                    res.release()
                except Exception:
                    pass
        if tex_override is not None:
            rid = id(tex_override)
            if rid not in seen and hasattr(tex_override, "release"):
                try:
                    tex_override.release()
                except Exception:
                    pass
        owner = str(payload.get("owner") or "").strip()
        if owner:
            try:
                proc_map = getattr(self, "_mgl_scene_proc_textures_by_owner", None)
                proc_entry = proc_map.pop(owner, None) if isinstance(proc_map, dict) else None
            except Exception:
                proc_entry = None
            if isinstance(proc_entry, dict):
                proc_tex = proc_entry.get("texture")
                if proc_tex is not None and id(proc_tex) not in seen and hasattr(proc_tex, "release"):
                    try:
                        proc_tex.release()
                    except Exception:
                        pass
        try:
            item.resources = []
        except Exception:
            pass

    def _mgl_fx_sync_instance_surface(self, payload) -> None:
        if self._mgl_ctx is None:
            return
        owner = str(payload.get("owner") or payload.get("target_owner") or "").strip()
        texture_path = str(payload.get("instance_texture") or "").strip()
        provider = payload.get("instance_texture_provider")
        provider_id = id(provider) if provider is not None else None
        current_provider_id = payload.get("_fx_instance_texture_provider_id")
        current_texture_path = str(payload.get("_fx_instance_texture_path") or "").strip()
        tex_override = payload.get("_fx_instance_texture")

        proc_map = getattr(self, "_mgl_scene_proc_textures_by_owner", None)
        if not isinstance(proc_map, dict):
            proc_map = {}
            self._mgl_scene_proc_textures_by_owner = proc_map

        if current_provider_id != provider_id and owner:
            old_entry = proc_map.pop(owner, None)
            if isinstance(old_entry, dict):
                old_tex = old_entry.get("texture")
                if old_tex is not None and old_tex is not tex_override and hasattr(old_tex, "release"):
                    try:
                        old_tex.release()
                    except Exception:
                        pass
            payload["_fx_instance_texture_provider_id"] = provider_id

        if provider is not None and owner:
            mesh_cache = payload.get("_fx_instance_mesh")
            mesh_subs = list((mesh_cache or {}).get("submeshes") or []) if isinstance(mesh_cache, dict) else []
            existing_entry = proc_map.get(owner) if isinstance(proc_map.get(owner), dict) else {}
            gpu_state = self._mgl_proc_state(provider)
            if gpu_state:
                rev = 0
                try:
                    rev = int(getattr(provider, "revision", 0))
                except Exception:
                    rev = 0
                proc_map[owner] = {
                    "provider": provider,
                    "texture": None,
                    "rev": rev,
                    "subs": mesh_subs,
                    "gpu": True,
                    "gpu_state": gpu_state,
                }
                try:
                    self._mgl_ensure_proc_glyph(gpu_state)
                except Exception:
                    pass
            else:
                qimg = self._mgl_provider_image(provider)
                proc_tex = existing_entry.get("texture")
                if qimg is not None and not qimg.isNull():
                    if hasattr(QtGui.QImage, "Format_RGBA8888"):
                        qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                    else:
                        qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                    qimg = qimg.mirrored(False, True)
                    try:
                        tex_size = (int(qimg.width()), int(qimg.height()))
                        if proc_tex is None or getattr(proc_tex, "size", None) != tex_size:
                            if proc_tex is not None and hasattr(proc_tex, "release"):
                                try:
                                    proc_tex.release()
                                except Exception:
                                    pass
                            proc_tex = self._mgl_make_texture(qimg)
                        else:
                            ptr = qimg.bits()
                            ptr.setsize(qimg.sizeInBytes())
                            proc_tex.write(bytes(ptr))
                            try:
                                proc_tex.build_mipmaps()
                            except Exception:
                                pass
                    except Exception:
                        proc_tex = None
                rev = 0
                try:
                    rev = int(getattr(provider, "revision", 0))
                except Exception:
                    rev = 0
                proc_map[owner] = {
                    "provider": provider,
                    "texture": proc_tex,
                    "rev": rev,
                    "subs": mesh_subs,
                }
            if tex_override is not None and hasattr(tex_override, "release"):
                try:
                    tex_override.release()
                except Exception:
                    pass
            payload.pop("_fx_instance_texture", None)
            payload["_fx_instance_texture_path"] = ""
            return

        if owner:
            old_entry = proc_map.pop(owner, None)
            if isinstance(old_entry, dict):
                old_tex = old_entry.get("texture")
                if old_tex is not None and old_tex is not tex_override and hasattr(old_tex, "release"):
                    try:
                        old_tex.release()
                    except Exception:
                        pass

        if not texture_path:
            if tex_override is not None and hasattr(tex_override, "release"):
                try:
                    tex_override.release()
                except Exception:
                    pass
            payload.pop("_fx_instance_texture", None)
            payload["_fx_instance_texture_path"] = ""
            return

        if texture_path != current_texture_path and tex_override is not None and hasattr(tex_override, "release"):
            try:
                tex_override.release()
            except Exception:
                pass
            tex_override = None
            payload.pop("_fx_instance_texture", None)

        if tex_override is None:
            try:
                tex_path = Path(texture_path)
            except Exception:
                tex_path = None
            if tex_path is not None and tex_path.exists():
                qimg = QtGui.QImage(str(tex_path))
                if not qimg.isNull():
                    if hasattr(QtGui.QImage, "Format_RGBA8888"):
                        qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                    else:
                        qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                    qimg = qimg.mirrored(False, True)
                    try:
                        tex_override = self._mgl_make_texture(qimg)
                    except Exception:
                        tex_override = None
        if tex_override is not None:
            payload["_fx_instance_texture"] = tex_override
            payload["_fx_instance_texture_path"] = texture_path

    def _mgl_fx_load_instance_mesh(self, path_text: str):
        if not _HAS_MGL or self._mgl_ctx is None:
            return None
        mesh_path = Path(str(path_text or "").strip())
        if not mesh_path.exists():
            return None
        ext = mesh_path.suffix.lower()
        mesh = None
        points = None
        normals = None
        uvs = None
        mesh_arrays = None
        bounds_points = None
        if openmesh is not None and ext != ".fbx":
            try:
                mesh = openmesh.read_trimesh(str(mesh_path))
            except Exception:
                mesh = None
        if mesh is None:
            if ext == ".fbx":
                try:
                    mesh_arrays = load_fbx_mesh_arrays_pyassimp(mesh_path)
                    points = mesh_arrays.points
                    normals = mesh_arrays.normals
                    uvs = mesh_arrays.uvs
                except Exception:
                    return None
            elif ext == ".obj":
                try:
                    points, normals, uvs = load_obj_mesh_arrays(mesh_path)
                except Exception:
                    points = None
            elif ext in (".gltf", ".glb"):
                try:
                    mesh_arrays = load_gltf_mesh_arrays(mesh_path)
                    points = mesh_arrays.points
                    normals = mesh_arrays.normals
                    uvs = mesh_arrays.uvs
                except Exception:
                    points = None
            if points is None:
                model_data = load_model(mesh_path)
                if model_data is None or not model_data.vertices:
                    return None
                points = np.array(model_data.vertices, dtype="f4").reshape(-1, 3)
                bounds_points = points
                normals = np.zeros_like(points)
                for idx in range(0, points.shape[0], 3):
                    a, b, c = points[idx:idx + 3]
                    n = np.cross(b - a, c - a)
                    norm = np.linalg.norm(n)
                    if norm > 1.0e-6:
                        n = n / norm
                    normals[idx:idx + 3] = n

        pivot_center = np.zeros(3, dtype=np.float32)
        pivot_radius = 1.0
        if mesh is not None:
            mesh.update_normals()
            points = np.array(mesh.points(), dtype="f4")
            bounds_points = points
            normals = np.array(mesh.vertex_normals(), dtype="f4")
            indices = np.array(mesh.face_vertex_indices(), dtype="u4").ravel()
            entry = self._mgl_build_mesh_entry(points, normals, None, indices=indices)
            if entry is None:
                return None
            try:
                mins = points.min(axis=0)
                maxs = points.max(axis=0)
                pivot_center = ((mins + maxs) * 0.5).astype(np.float32)
                pivot_radius = max(1.0e-4, float(np.max(np.abs(maxs - mins))) * 0.5)
            except Exception:
                pass
            resources = [
                entry.get("vao"),
                entry.get("vbo"),
                entry.get("nbo"),
                entry.get("tbo"),
                entry.get("cbo"),
                entry.get("ibo"),
            ]
            return {
                "vao": entry.get("vao"),
                "mesh_entry": entry,
                "color": self._mgl_mesh_color,
                "path": str(mesh_path),
                "pivot_center": pivot_center,
                "pivot_radius": float(pivot_radius),
                "resources": [res for res in resources if res is not None],
            }

        if mesh_arrays is not None and mesh_arrays.submeshes:
            entries, _combined_uvs, _texture_paths, _total_indices = self._mgl_build_submesh_entries(mesh_arrays.submeshes)
            if not entries:
                return None
            try:
                all_points = []
                for sub in mesh_arrays.submeshes:
                    pts = getattr(sub, "points", None)
                    if pts is not None and getattr(pts, "size", 0):
                        all_points.append(np.asarray(pts, dtype="f4").reshape(-1, 3))
                if all_points:
                    bounds_points = np.concatenate(all_points, axis=0)
            except Exception:
                bounds_points = None
            try:
                if bounds_points is not None and getattr(bounds_points, "size", 0):
                    mins = bounds_points.min(axis=0)
                    maxs = bounds_points.max(axis=0)
                    pivot_center = ((mins + maxs) * 0.5).astype(np.float32)
                    pivot_radius = max(1.0e-4, float(np.max(np.abs(maxs - mins))) * 0.5)
            except Exception:
                pass
            resources = []
            seen = set()
            for sub in entries:
                for res in (
                    sub.get("vao"),
                    sub.get("vbo"),
                    sub.get("nbo"),
                    sub.get("tbo"),
                    sub.get("cbo"),
                    sub.get("ibo"),
                    sub.get("texture"),
                ):
                    if res is None:
                        continue
                    rid = id(res)
                    if rid in seen:
                        continue
                    seen.add(rid)
                    resources.append(res)
            return {
                "submeshes": entries,
                "path": str(mesh_path),
                "pivot_center": pivot_center,
                "pivot_radius": float(pivot_radius),
                "resources": resources,
            }

        entry = self._mgl_build_mesh_entry(points, normals, uvs)
        if entry is None:
            return None
        try:
            if bounds_points is None:
                bounds_points = np.asarray(points, dtype="f4").reshape(-1, 3)
            if bounds_points is not None and getattr(bounds_points, "size", 0):
                mins = bounds_points.min(axis=0)
                maxs = bounds_points.max(axis=0)
                pivot_center = ((mins + maxs) * 0.5).astype(np.float32)
                pivot_radius = max(1.0e-4, float(np.max(np.abs(maxs - mins))) * 0.5)
        except Exception:
            pass
        resources = [
            entry.get("vao"),
            entry.get("vbo"),
            entry.get("nbo"),
            entry.get("tbo"),
            entry.get("cbo"),
            entry.get("ibo"),
        ]
        color = (
            mesh_arrays.base_color
            if mesh_arrays is not None and mesh_arrays.base_color is not None
            else self._mgl_mesh_color
        )
        return {
            "vao": entry.get("vao"),
            "mesh_entry": entry,
            "texture": None,
            "color": color,
            "path": str(mesh_path),
            "pivot_center": pivot_center,
            "pivot_radius": float(pivot_radius),
            "resources": [res for res in resources if res is not None],
        }

    def _mgl_fx_build_trail_mesh_models(self, payload, frame: int):
        if np is None:
            self._mgl_fx_log_throttled("numpy-missing", "[renderer] build skip: numpy unavailable", interval=3.0)
            return None
        target_owner = str(payload.get("target_owner") or "").strip()
        active_owner = self._mgl_fx_pick_owner(payload) or target_owner
        payload["_fx_active_owner"] = active_owner
        if not active_owner:
            owner = str(payload.get("owner") or "").strip()
            self._mgl_fx_log_throttled(
                f"missing-target:{owner or 'unknown'}",
                f"[renderer] build skip: missing target owner owner={owner!r}",
                interval=1.0,
            )
            return None
        try:
            enabled = bool(payload.get("enabled", True))
        except Exception:
            enabled = True
        if not enabled:
            return None
        try:
            samples = max(1, int(payload.get("samples", 28)))
        except Exception:
            samples = 28
        try:
            lifespan = max(1, int(payload.get("lifespan", int(samples))))
        except Exception:
            lifespan = max(1, int(samples))
        spawn_interval = float(self._mgl_fx_spawn_interval(payload))
        try:
            base_radius = max(0.001, float(payload.get("radius", 0.35)))
        except Exception:
            base_radius = 0.35
        try:
            bounds_getter = getattr(self, "get_scene_owner_bounds", None)
            if callable(bounds_getter):
                bmin, bmax = bounds_getter(active_owner)
                if bmin is not None and bmax is not None:
                    extents = np.asarray(bmax, dtype=np.float32) - np.asarray(bmin, dtype=np.float32)
                    owner_scale = max(0.0, float(np.max(np.abs(extents))) * 0.5)
                    if owner_scale > 1.0:
                        base_radius = max(float(base_radius), float(base_radius) * float(owner_scale))
        except Exception:
            pass

        entries = self._mgl_fx_sample_entries(payload, frame, active_owner)
        if not entries:
            payload["_fx_debug_oldest_center"] = None
            payload["_fx_debug_newest_center"] = None
            payload["_fx_debug_oldest_frame"] = None
            payload["_fx_debug_newest_frame"] = None
            return None
        entries = list(reversed(entries))
        try:
            oldest_entry = entries[0]
            newest_entry = entries[-1]
            payload["_fx_debug_oldest_center"] = tuple(float(v) for v in oldest_entry["center"])
            payload["_fx_debug_newest_center"] = tuple(float(v) for v in newest_entry["center"])
            payload["_fx_debug_oldest_frame"] = float(oldest_entry["frame"])
            payload["_fx_debug_newest_frame"] = float(newest_entry["frame"])
        except Exception:
            payload["_fx_debug_oldest_center"] = None
            payload["_fx_debug_newest_center"] = None
            payload["_fx_debug_oldest_frame"] = None
            payload["_fx_debug_newest_frame"] = None

        models = []
        mesh_cache = payload.get("_fx_instance_mesh")
        if isinstance(mesh_cache, dict):
            try:
                pivot_center = np.asarray(mesh_cache.get("pivot_center") or (0.0, 0.0, 0.0), dtype=np.float32).reshape(3)
            except Exception:
                pivot_center = np.zeros(3, dtype=np.float32)
            try:
                pivot_radius = max(1.0e-4, float(mesh_cache.get("pivot_radius", 1.0) or 1.0))
            except Exception:
                pivot_radius = 1.0
        else:
            pivot_center = np.zeros(3, dtype=np.float32)
            pivot_radius = 1.0

        raw_xform = payload.get("instance_xform")
        if isinstance(raw_xform, dict):
            try:
                base_pos = tuple(float(v) for v in (raw_xform.get("pos") or (0.0, 0.0, 0.0)))
            except Exception:
                base_pos = (0.0, 0.0, 0.0)
            try:
                base_rot = tuple(float(v) for v in (raw_xform.get("rot") or (0.0, 0.0, 0.0)))
            except Exception:
                base_rot = (0.0, 0.0, 0.0)
            try:
                base_scl = tuple(float(v) for v in (raw_xform.get("scl") or (1.0, 1.0, 1.0)))
            except Exception:
                base_scl = (1.0, 1.0, 1.0)
        else:
            base_pos = (0.0, 0.0, 0.0)
            base_rot = (0.0, 0.0, 0.0)
            base_scl = (1.0, 1.0, 1.0)

        def _t(tx: float, ty: float, tz: float):
            m = np.eye(4, dtype=np.float32)
            m[3, 0] = float(tx)
            m[3, 1] = float(ty)
            m[3, 2] = float(tz)
            return m

        def _s(sx: float, sy: float, sz: float):
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = float(sx)
            m[1, 1] = float(sy)
            m[2, 2] = float(sz)
            return m

        def _rx(angle_deg: float):
            ang = math.radians(float(angle_deg))
            c = math.cos(ang)
            s = math.sin(ang)
            m = np.eye(4, dtype=np.float32)
            m[1, 1] = c
            m[1, 2] = s
            m[2, 1] = -s
            m[2, 2] = c
            return m

        def _ry(angle_deg: float):
            ang = math.radians(float(angle_deg))
            c = math.cos(ang)
            s = math.sin(ang)
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = c
            m[0, 2] = -s
            m[2, 0] = s
            m[2, 2] = c
            return m

        def _rz(angle_deg: float):
            ang = math.radians(float(angle_deg))
            c = math.cos(ang)
            s = math.sin(ang)
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = c
            m[0, 1] = s
            m[1, 0] = -s
            m[1, 1] = c
            return m

        base_rot_mat = _rx(-float(base_rot[0])) @ _ry(-float(base_rot[1])) @ _rz(-float(base_rot[2]))

        for entry in entries:
            phase_t = self._mgl_fx_profile_phase(
                payload,
                int(entry.get("emit_index", 0)),
                float(spawn_interval),
                int(lifespan),
            )
            profile_val = self._mgl_fx_profile_value(payload, phase_t)
            age_scale = max(0.0, float(self._mgl_fx_age_scale_value(payload, float(entry["age"]), int(lifespan))))
            uniform_scale = float(base_radius) * max(0.08, float(profile_val)) * age_scale
            if uniform_scale <= 1.0e-5:
                continue
            center = entry["center"]
            normalized_scale = float(uniform_scale) / float(pivot_radius)
            model = (
                _t(-float(pivot_center[0]), -float(pivot_center[1]), -float(pivot_center[2]))
                @ base_rot_mat
                @ _s(
                    float(base_scl[0]) * normalized_scale,
                    float(base_scl[1]) * normalized_scale,
                    float(base_scl[2]) * normalized_scale,
                )
                @ _t(
                    float(center[0]) + float(base_pos[0]),
                    float(center[1]) + float(base_pos[1]),
                    float(center[2]) + float(base_pos[2]),
                )
            )
            models.append(model)
        return models

    def _mgl_fx_update_trail_item(self, item: MGLSceneItem) -> bool:
        payload = item.payload or {}
        frame_fn = getattr(self, "_timeline_current_frame", None)
        if not callable(frame_fn):
            self._mgl_fx_log_throttled(
                "timeline-frame-missing",
                "[renderer] update skip: timeline frame function unavailable",
                interval=3.0,
            )
            return False
        try:
            frame = int(frame_fn())
        except Exception:
            frame = 0
        target_owner = str(payload.get("target_owner") or "").strip()
        active_owner = self._mgl_fx_pick_owner(payload) or target_owner
        payload["_fx_active_owner"] = active_owner
        live_pos = self._mgl_fx_current_owner_pos(active_owner)
        allow_instances = bool(self._mgl_timeline_fx_enabled())
        show_proxy = bool(getattr(self, "_timeline_fx_proxy_enabled", True))
        instance_path = str(payload.get("instance_path") or "").strip() if allow_instances else ""
        stamp = (
            int(frame),
            tuple(round(float(v), 5) for v in live_pos) if isinstance(live_pos, (list, tuple)) else None,
            bool(payload.get("enabled", True)),
            bool(payload.get("global_space", False)),
            int(payload.get("samples", 28)),
            int(payload.get("frame_step", 1)),
            round(float(payload.get("spawn_rate", payload.get("frame_step", 1.0))), 5),
            int(payload.get("substeps", 1)),
            int(payload.get("lifespan", int(payload.get("samples", 28)) * max(1, int(payload.get("frame_step", 1))))),
            int(payload.get("repeats", 1)),
            round(float(payload.get("radius", 0.35)), 5),
            int(payload.get("sides", 28)),
            round(float(payload.get("line_width", 2.0)), 3),
            repr(payload.get("profile_points")),
            round(float(payload.get("age_scale_min", 1.0)), 5),
            round(float(payload.get("age_scale_max", 1.0)), 5),
            repr(payload.get("age_scale_points")),
            tuple(self._mgl_fx_owner_candidates(payload)),
            bool(allow_instances),
            bool(show_proxy),
            instance_path,
            repr(dict(payload.get("instance_material") or {})),
            str(payload.get("instance_texture") or ""),
            id(payload.get("instance_texture_provider")) if payload.get("instance_texture_provider") is not None else None,
        )
        if payload.get("_fx_stamp") == stamp:
            if instance_path:
                if payload.get("_fx_instance_models") and isinstance(payload.get("_fx_instance_mesh"), dict):
                    return True
            elif bool(show_proxy) and payload.get("vao") is not None:
                return True
            elif not bool(show_proxy):
                return False

        if instance_path:
            loaded_path = str(payload.get("_fx_instance_loaded_path") or "").strip()
            if loaded_path and loaded_path != instance_path:
                self._mgl_fx_release_mesh_cache(item, payload)
            if payload.get("vao") is not None:
                for res in list(getattr(item, "resources", None) or []):
                    if res is not None and hasattr(res, "release"):
                        try:
                            res.release()
                        except Exception:
                            pass
                item.resources = []
                payload.pop("vao", None)
                payload.pop("mode", None)
            mesh_cache = payload.get("_fx_instance_mesh")
            if not isinstance(mesh_cache, dict):
                mesh_cache = self._mgl_fx_load_instance_mesh(instance_path)
                if isinstance(mesh_cache, dict):
                    payload["_fx_instance_mesh"] = mesh_cache
                    payload["_fx_instance_loaded_path"] = instance_path
            self._mgl_fx_sync_instance_surface(payload)
            resources = list(mesh_cache.get("resources") or []) if isinstance(mesh_cache, dict) else []
            tex_override = payload.get("_fx_instance_texture")
            if tex_override is not None:
                seen_ids = {id(res) for res in resources}
                if id(tex_override) not in seen_ids:
                    resources.append(tex_override)
            item.resources = resources
            if not isinstance(mesh_cache, dict):
                payload["_fx_stamp"] = stamp
                item.payload = payload
                self._mgl_fx_log(
                    f"[renderer] update mesh-load-failed owner={item.name} target_owner={target_owner} "
                    f"active_owner={active_owner} frame={int(frame)} instance={instance_path!r}"
                )
                return False
            models = self._mgl_fx_build_trail_mesh_models(payload, frame)
            if not models:
                payload["_fx_instance_models"] = []
                payload["_fx_stamp"] = stamp
                item.payload = payload
                self._mgl_fx_log(
                    f"[renderer] update no-instance-models owner={item.name} target_owner={target_owner} "
                    f"active_owner={active_owner} frame={int(frame)} live_pos={live_pos!r} "
                    f"instance={instance_path!r} aliases={self._mgl_fx_owner_candidates(payload)!r}"
                )
                return False
            payload["_fx_instance_models"] = list(models)
            payload["_fx_stamp"] = stamp
            item.payload = payload
            self._mgl_fx_log(
                f"[renderer] update ok owner={item.name} target_owner={target_owner} active_owner={active_owner} "
                f"frame={int(frame)} instance_count={int(len(models))} instance={instance_path!r} "
                f"visible={bool(item.visible)} live_pos={live_pos!r} "
                f"newest_frame={payload.get('_fx_debug_newest_frame')!r} newest_center={payload.get('_fx_debug_newest_center')!r} "
                f"oldest_frame={payload.get('_fx_debug_oldest_frame')!r} oldest_center={payload.get('_fx_debug_oldest_center')!r} "
                f"global={bool(payload.get('global_space', False))} "
                f"spawn_rate={float(payload.get('spawn_rate', payload.get('frame_step', 1.0)) or 1.0):.3f} "
                f"substeps={int(payload.get('substeps', 1) or 1)} repeats={int(payload.get('repeats', 1) or 1)} "
                f"aliases={self._mgl_fx_owner_candidates(payload)!r}"
            )
            return True

        if isinstance(payload.get("_fx_instance_mesh"), dict):
            self._mgl_fx_release_mesh_cache(item, payload)

        if not bool(show_proxy):
            for res in list(getattr(item, "resources", None) or []):
                if res is not None and hasattr(res, "release"):
                    try:
                        res.release()
                    except Exception:
                        pass
            item.resources = []
            payload.pop("vao", None)
            payload.pop("mode", None)
            payload["_fx_stamp"] = stamp
            item.payload = payload
            return False

        line_points = self._mgl_fx_build_trail_line_points(payload, frame)
        for res in list(getattr(item, "resources", None) or []):
            if res is not None and hasattr(res, "release"):
                try:
                    res.release()
                except Exception:
                    pass
        item.resources = []
        payload.pop("vao", None)
        payload.pop("mode", None)
        if line_points is None or getattr(line_points, "size", 0) == 0:
            payload["_fx_stamp"] = stamp
            item.payload = payload
            self._mgl_fx_log(
                f"[renderer] update no-geometry owner={item.name} target_owner={target_owner} "
                f"active_owner={active_owner} frame={int(frame)} live_pos={live_pos!r} "
                f"instance='<rings>' "
                f"global={bool(payload.get('global_space', False))} "
                f"spawn_rate={float(payload.get('spawn_rate', payload.get('frame_step', 1.0)) or 1.0):.3f} "
                f"substeps={int(payload.get('substeps', 1) or 1)} repeats={int(payload.get('repeats', 1) or 1)} "
                f"aliases={self._mgl_fx_owner_candidates(payload)!r}"
            )
            return False

        temp = self._mgl_add_wire_item_from_points(
            name=item.name,
            line_points=line_points,
            visible=item.visible,
            tag=str(getattr(item, "tag", "") or "scene-fx-trail"),
            owner=str(payload.get("owner") or payload.get("target_owner") or ""),
            path_key=str(payload.get("path") or ""),
        )
        if temp is None:
            payload["_fx_stamp"] = stamp
            item.payload = payload
            self._mgl_fx_log(
                f"[renderer] update gpu-build-failed owner={item.name} target_owner={target_owner} "
                f"active_owner={active_owner} "
                f"instance='<rings>' "
                f"global={bool(payload.get('global_space', False))} "
                f"spawn_rate={float(payload.get('spawn_rate', payload.get('frame_step', 1.0)) or 1.0):.3f} "
                f"substeps={int(payload.get('substeps', 1) or 1)} repeats={int(payload.get('repeats', 1) or 1)} "
                f"frame={int(frame)} point_count={int(getattr(line_points, 'shape', [0])[0])}"
            )
            return False

        dyn_payload = temp.payload or {}
        payload["vao"] = dyn_payload.get("vao")
        payload["mode"] = dyn_payload.get("mode")
        payload["color"] = self._mgl_fx_color_rgba(payload.get("color"))
        payload["line_width"] = float(payload.get("line_width", 2.0) or 2.0)
        payload["_fx_stamp"] = stamp
        item.payload = payload
        item.resources = list(temp.resources or [])
        try:
            point_count = int(line_points.shape[0])
        except Exception:
            point_count = 0
        self._mgl_fx_log(
            f"[renderer] update ok owner={item.name} target_owner={target_owner} active_owner={active_owner} "
            f"frame={int(frame)} point_count={point_count} instance='<rings>' visible={bool(item.visible)} live_pos={live_pos!r} "
            f"newest_frame={payload.get('_fx_debug_newest_frame')!r} newest_center={payload.get('_fx_debug_newest_center')!r} "
            f"oldest_frame={payload.get('_fx_debug_oldest_frame')!r} oldest_center={payload.get('_fx_debug_oldest_center')!r} "
            f"global={bool(payload.get('global_space', False))} "
            f"spawn_rate={float(payload.get('spawn_rate', payload.get('frame_step', 1.0)) or 1.0):.3f} "
            f"substeps={int(payload.get('substeps', 1) or 1)} repeats={int(payload.get('repeats', 1) or 1)} "
            f"aliases={self._mgl_fx_owner_candidates(payload)!r}"
        )
        return payload.get("vao") is not None

    def _mgl_lookup_owner_entry(self, mapping, owner: str):
        if not isinstance(mapping, dict):
            return None, None
        key = str(owner or "").strip()
        if not key:
            return None, None
        if key in mapping:
            return key, mapping.get(key)
        lk = key.lower()
        for k, v in mapping.items():
            try:
                if str(k).strip().lower() == lk:
                    return k, v
            except Exception:
                continue
        return None, None

    def _mgl_get_scene_asset_xform(self, owner: str):
        owner_key = str(owner or "").strip()
        d = getattr(self, "_mgl_scene_xforms_by_owner", None)
        if not isinstance(d, dict):
            d = {}
            setattr(self, "_mgl_scene_xforms_by_owner", d)
        _, x = self._mgl_lookup_owner_entry(d, owner_key)
        if isinstance(x, dict):
            return x
        # Shelf-side owner routing can briefly call the mesh getter for splat owners.
        splat_map = getattr(self, "_mgl_scene_splat_xforms_by_owner", None)
        _, sx = self._mgl_lookup_owner_entry(splat_map, owner_key)
        if isinstance(sx, dict):
            return sx
        return {"pos": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0), "scl": (1.0, 1.0, 1.0)}

    def _mgl_get_scene_splat_xform(self, owner: str):
        owner_key = str(owner or "").strip()
        d = getattr(self, "_mgl_scene_splat_xforms_by_owner", None)
        if not isinstance(d, dict):
            d = {}
            setattr(self, "_mgl_scene_splat_xforms_by_owner", d)
        _, x = self._mgl_lookup_owner_entry(d, owner_key)
        if isinstance(x, dict):
            return x
        # Mirror mesh lookup fallback for mixed owner-map callers.
        mesh_map = getattr(self, "_mgl_scene_xforms_by_owner", None)
        _, mx = self._mgl_lookup_owner_entry(mesh_map, owner_key)
        if isinstance(mx, dict):
            return mx
        return {"pos": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0), "scl": (1.0, 1.0, 1.0)}

    def _mgl_set_scene_asset_xform(
        self,
        owner: str,
        pos=None,
        rot=None,
        scl=None,
        apply_to_scene_models: bool = True,
        use_splat_xform: bool = False,
    ) -> None:
        if not owner:
            return
        if np is None:
            return
        owner_norm = str(owner).strip().lower()

        if use_splat_xform:
            splat_xforms = getattr(self, "_mgl_scene_splat_xforms_by_owner", None)
            if not isinstance(splat_xforms, dict):
                splat_xforms = {}
                setattr(self, "_mgl_scene_splat_xforms_by_owner", splat_xforms)
            _, existing_splat_xf = self._mgl_lookup_owner_entry(splat_xforms, owner)
            if isinstance(existing_splat_xf, dict):
                x = existing_splat_xf
            else:
                mesh_xf = self._mgl_get_scene_asset_xform(owner)
                if isinstance(mesh_xf, dict):
                    x = {
                        "pos": tuple(mesh_xf.get("pos", (0.0, 0.0, 0.0))),
                        "rot": tuple(mesh_xf.get("rot", (0.0, 0.0, 0.0))),
                        "scl": tuple(mesh_xf.get("scl", (1.0, 1.0, 1.0))),
                    }
                else:
                    x = {"pos": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0), "scl": (1.0, 1.0, 1.0)}
                splat_xforms[str(owner).strip()] = x
        else:
            mesh_xforms = getattr(self, "_mgl_scene_xforms_by_owner", None)
            if not isinstance(mesh_xforms, dict):
                mesh_xforms = {}
                setattr(self, "_mgl_scene_xforms_by_owner", mesh_xforms)
            _, existing_mesh_xf = self._mgl_lookup_owner_entry(mesh_xforms, owner)
            if isinstance(existing_mesh_xf, dict):
                x = existing_mesh_xf
            else:
                mesh_xf = self._mgl_get_scene_asset_xform(owner)
                if isinstance(mesh_xf, dict):
                    x = {
                        "pos": tuple(mesh_xf.get("pos", (0.0, 0.0, 0.0))),
                        "rot": tuple(mesh_xf.get("rot", (0.0, 0.0, 0.0))),
                        "scl": tuple(mesh_xf.get("scl", (1.0, 1.0, 1.0))),
                    }
                else:
                    x = {"pos": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0), "scl": (1.0, 1.0, 1.0)}
                mesh_xforms[str(owner).strip()] = x
        if pos is not None:
            x["pos"] = tuple(float(v) for v in pos)
        if rot is not None:
            x["rot"] = tuple(float(v) for v in rot)
        if scl is not None:
            x["scl"] = tuple(float(v) for v in scl)

        try:
            light_owner = str(getattr(self, "_mgl_scene_light_owner", "") or "").strip().lower()
            if light_owner and owner_norm == light_owner:
                pos_vals = list(x.get("pos", (0.0, 0.0, 0.0)))[:3]
                if len(pos_vals) >= 3:
                    self._mgl_scene_light_pos = (
                        float(pos_vals[0]),
                        float(pos_vals[1]),
                        float(pos_vals[2]),
                    )
        except Exception:
            pass

        # pivot around asset bounds center if we have it
        cx = cy = cz = 0.0
        offset_mode = False
        try:
            bounds_map = None
            if use_splat_xform:
                bounds_map = (
                    getattr(self, "_mgl_scene_splats_bounds_local", None)
                    or getattr(self, "_mgl_scene_splat_bounds_by_owner", None)
                )
            elif apply_to_scene_models:
                bounds_map = (
                    getattr(self, "_mgl_scene_mesh_bounds_by_owner", None)
                    or getattr(self, "_mgl_scene_bounds_by_owner", None)
                )
            else:
                bounds_map = getattr(self, "_mgl_scene_bounds_by_owner", None)

            b = None
            if isinstance(bounds_map, dict):
                _, b = self._mgl_lookup_owner_entry(bounds_map, owner)
            if b is not None:
                bmin, bmax = b
                cx, cy, cz = self._mgl_owner_pivot_local(owner, bmin, bmax)
        except Exception:
            pass
        try:
            offset_map = getattr(self, "_mgl_scene_xform_offset_by_owner", None)
            if isinstance(offset_map, dict) and apply_to_scene_models and not use_splat_xform:
                if owner in offset_map:
                    offset_mode = True
                else:
                    lo = str(owner).strip().lower()
                    for k in offset_map.keys():
                        try:
                            if str(k).strip().lower() == lo:
                                offset_mode = True
                                break
                        except Exception:
                            continue
        except Exception:
            offset_mode = False

        px, py, pz = x["pos"]
        rx, ry, rz = x["rot"]  # degrees
        sx, sy, sz = x["scl"]
        if offset_mode:
            try:
                px = float(px) + cx
                py = float(py) + cy
                pz = float(pz) + cz
            except Exception:
                pass

        # IMPORTANT:
        # gl_view/gizmo stores rot_deg using its negated-angle convention.
        # Match that here for scene meshes so they follow the gizmo orientation.
        rot_rx, rot_ry, rot_rz = rx, ry, rz
        if apply_to_scene_models and (not use_splat_xform):
            try:
                rot_rx = -float(rx)
                rot_ry = -float(ry)
                rot_rz = -float(rz)
            except Exception:
                pass


        def T(tx, ty, tz):
            m = np.eye(4, dtype=np.float32)
            m[3, 0] = tx
            m[3, 1] = ty
            m[3, 2] = tz
            return m

        def S(sx, sy, sz):
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = sx
            m[1, 1] = sy
            m[2, 2] = sz
            return m

        def Rx(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[1, 1] = c
            m[1, 2] = s
            m[2, 1] = -s
            m[2, 2] = c
            return m

        def Ry(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = c
            m[0, 2] = -s
            m[2, 0] = s
            m[2, 2] = c
            return m

        def Rz(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = c
            m[0, 1] = s
            m[1, 0] = -s
            m[1, 1] = c
            return m


        # Build rotation in row-vector order to match gl_view's column-vector convention.
        R = (Rx(rot_rx) @ Ry(rot_ry) @ Rz(rot_rz))

        xform_space = str(getattr(self, "_mgl_xform_space", "world") or "world").lower()
        if xform_space == "local":
            model = T(-cx, -cy, -cz) @ S(sx, sy, sz) @ R @ T(px, py, pz)
        else:
            model = T(-cx, -cy, -cz) @ R @ S(sx, sy, sz) @ T(px, py, pz)

        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            return

        # apply to matching scene items (solid + wire)
        if apply_to_scene_models:
            try:
                for tag in (
                    "scene-model",
                    "scene-wire",
                    "scene-rig-joints",
                    "scene-volume",
                    "scene-camera",
                    "scene-light",
                    "retarget-handles",
                    "retarget-selection",
                    "scene-skeleton-handles",
                ):
                    for item in scene.iter_by_tag(tag):
                        payload = getattr(item, "payload", None) or {}
                        item_owner = str(payload.get("owner") or "").strip().lower()
                        if item_owner != owner_norm:
                            continue
                        if tag == "scene-skeleton-handles":
                            continue
                        if tag == "scene-rig-joints" and bool(payload.get("scene_skeleton_overlay", False)):
                            continue
                        payload["model"] = model
                        try:
                            item.payload = payload
                        except Exception:
                            pass
            except Exception:
                pass

        # IMPORTANT: if this owner is a splat owner, rebuild splat buffer so translation applies
        try:
            splat_map = getattr(self, "_mgl_scene_splats", None) or {}
            owner_s = str(owner).strip()
            is_splat = False
            if isinstance(splat_map, dict) and splat_map:
                if owner in splat_map or owner_s in splat_map:
                    is_splat = True
                else:
                    lo = owner_s.lower()
                    for k in splat_map.keys():
                        if str(k).strip().lower() == lo:
                            is_splat = True
                            break
            if is_splat:
                try:
                    # Mark splats dirty; rebuild in GL paint (safe).
                    setattr(self, "_mgl_splats_need_rebuild", True)
                    setattr(self, "_mgl_splats_visibility_dirty", True)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._mgl_shadow_dirty = True
            self._mgl_shadow_valid = False
            self._mgl_shadow_signature = None
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass

    def _mgl_rebuild_scene_splats(self, preserve_camera: bool = False) -> None:
        if np is None:
            self._mgl_disable_splats()
            return

        splat_map = getattr(self, "_mgl_scene_splats", None) or {}
        if not splat_map:
            self._mgl_disable_splats()
            return

        visibility = getattr(self, "_mgl_scene_visibility", {}) or {}
        try:
            self._mgl_log_throttled(
                "_mgl_splat_log_rebuild_start_ts",
                "splats: rebuild start owners="
                + str(len(splat_map))
                + " visible="
                + str(sum(1 for k in splat_map.keys() if visibility.get(k, True)))
                + " preserve_camera="
                + str(bool(preserve_camera)),
                1.0,
            )
        except Exception:
            pass
        xforms = getattr(self, "_mgl_scene_splat_xforms_by_owner", None)
        if not isinstance(xforms, dict):
            xforms = {}
            try:
                self._mgl_scene_splat_xforms_by_owner = xforms
            except Exception:
                pass
        bounds_local = getattr(self, "_mgl_scene_splats_bounds_local", None)
        if not isinstance(bounds_local, dict):
            bounds_local = {}
            try:
                self._mgl_scene_splats_bounds_local = bounds_local
            except Exception:
                pass
        bounds_by_owner = getattr(self, "_mgl_scene_splat_bounds_by_owner", None)
        if not isinstance(bounds_by_owner, dict):
            bounds_by_owner = {}
            try:
                self._mgl_scene_splat_bounds_by_owner = bounds_by_owner
            except Exception:
                pass
        glow_by_owner = getattr(self, "_mgl_scene_splat_glow_by_owner", None)
        if not isinstance(glow_by_owner, dict):
            glow_by_owner = {}
            try:
                self._mgl_scene_splat_glow_by_owner = glow_by_owner
            except Exception:
                pass

        # normalized lookup for xforms
        xforms_norm = {}
        if isinstance(xforms, dict):
            for k, v in xforms.items():
                if k is None:
                    continue
                ks = str(k).strip()
                xforms_norm[ks] = v
                xforms_norm[ks.lower()] = v

        state = self._mgl_get_camera_state() if preserve_camera else None
        # Avoid feeding scene_xforms back into splat rebuild (prevents feedback loops).
        if isinstance(state, dict):
            try:
                state.pop("scene_xforms", None)
            except Exception:
                pass

        def _quat_mul(a, b):
            # a,b = (x,y,z,w)
            ax, ay, az, aw = a
            bx, by, bz, bw = b
            return np.array(
                [
                    aw * bx + ax * bw + ay * bz - az * by,
                    aw * by - ax * bz + ay * bw + az * bx,
                    aw * bz + ax * by - ay * bx + az * bw,
                    aw * bw - ax * bx - ay * by - az * bz,
                ],
                dtype=np.float32,
            )

        def _quat_from_euler_deg(rx, ry, rz):
            # match mesh order: Rz @ Ry @ Rx
            hx = math.radians(rx) * 0.5
            hy = math.radians(ry) * 0.5
            hz = math.radians(rz) * 0.5

            sx, cx = math.sin(hx), math.cos(hx)
            sy, cy = math.sin(hy), math.cos(hy)
            sz, cz = math.sin(hz), math.cos(hz)

            qx = np.array([sx, 0.0, 0.0, cx], dtype=np.float32)
            qy = np.array([0.0, sy, 0.0, cy], dtype=np.float32)
            qz = np.array([0.0, 0.0, sz, cz], dtype=np.float32)

            return _quat_mul(_quat_mul(qz, qy), qx)

        def _quat_rotate_vec(q, v):
            # v' = q * (v,0) * conj(q)
            x, y, z, w = q
            qv = np.array([v[0], v[1], v[2], 0.0], dtype=np.float32)
            qc = np.array([-x, -y, -z, w], dtype=np.float32)
            return _quat_mul(_quat_mul(q, qv), qc)[:3]

        def _to_15(arr):
            a = np.asarray(arr, dtype=np.float32)
            if a.ndim != 2 or a.shape[1] not in (8, 10, 14, 15):
                return None

            if a.shape[1] == 15:
                return a

            n = a.shape[0]
            if a.shape[1] == 8:
                # [pos3 col4 rad1] -> add scale3=1 and quat=(0,0,0,1)
                scale3 = np.ones((n, 3), dtype=np.float32)
                quat = np.zeros((n, 4), dtype=np.float32)
                quat[:, 3] = 1.0
                return np.concatenate([a[:, 0:8], scale3, quat], axis=1)

            if a.shape[1] == 10:
                # [pos3 col4 rad1 sx sy] -> add sz=1 and quat=(0,0,0,1)
                sz = np.ones((n, 1), dtype=np.float32)
                scale3 = np.concatenate([a[:, 8:10], sz], axis=1)
                quat = np.zeros((n, 4), dtype=np.float32)
                quat[:, 3] = 1.0
                return np.concatenate([a[:, 0:8], scale3, quat], axis=1)

            # 14: [pos3 col4 rad1 sx sy qx qy qz qw] -> add sz=1
            sz = np.ones((n, 1), dtype=np.float32)
            scale3 = np.concatenate([a[:, 8:10], sz], axis=1)
            quat = a[:, 10:14]
            return np.concatenate([a[:, 0:8], scale3, quat], axis=1)

        arrays15 = []
        lit_arrays = []
        glow_arrays = []
        lit_splat_owners = set()
        unlit_splat_owners = set()
        skinned_proxy_owners = set()
        try:
            proxies = getattr(self, "_mgl_scene_skinned_splat_proxies_by_owner", None)
            if isinstance(proxies, dict):
                skinned_proxy_owners = {str(k).strip().lower() for k in proxies.keys()}
        except Exception:
            skinned_proxy_owners = set()
        splats_world = {}
        for owner, arr in splat_map.items():
            if not visibility.get(owner, True):
                continue
            if arr is None or getattr(arr, "size", 0) == 0:
                continue

            a15 = _to_15(arr)
            if a15 is None or a15.size == 0:
                continue

            # find xf
            xf = {}
            try:
                if isinstance(xforms, dict) and owner in xforms:
                    xf = xforms.get(owner) or {}
                else:
                    key = str(owner).strip()
                    xf = xforms_norm.get(key) or xforms_norm.get(key.lower()) or {}
                    if not xf and key:
                        for k2, v2 in xforms_norm.items():
                            if k2.endswith(key) or key.endswith(k2):
                                xf = v2 or {}
                                break
            except Exception:
                xf = {}

            try:
                pos = (xf.get("pos") if isinstance(xf, dict) else None) or (0.0, 0.0, 0.0)
                rot = (xf.get("rot") if isinstance(xf, dict) else None) or (0.0, 0.0, 0.0)
                scl = (xf.get("scl") if isinstance(xf, dict) else None) or (1.0, 1.0, 1.0)
                px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
                rx, ry, rz = float(rot[0]), float(rot[1]), float(rot[2])
                sx, sy, sz = float(scl[0]), float(scl[1]), float(scl[2])
            except Exception:
                px = py = pz = 0.0
                rx = ry = rz = 0.0
                sx = sy = sz = 1.0

            # pivot = owner override -> LOCAL bounds center -> current center
            try:
                bmin = bmax = None
                b = (bounds_local or {}).get(owner)
                if b is not None:
                    bmin, bmax = b
                piv_map = getattr(self, "_mgl_scene_pivot_local_by_owner", None)
                piv_override = self._mgl_casefold_get(piv_map, owner)
                if isinstance(piv_override, (list, tuple)) and len(piv_override) >= 3:
                    pivot = np.array(
                        [float(piv_override[0]), float(piv_override[1]), float(piv_override[2])],
                        dtype=np.float32,
                    )
                elif bmin is not None and bmax is not None:
                    pivot = ((bmin + bmax) * 0.5).astype(np.float32)
                else:
                    pivot = a15[:, :3].mean(axis=0).astype(np.float32)
            except Exception:
                pivot = a15[:, :3].mean(axis=0).astype(np.float32)

            # apply scale+rot around pivot to positions
            if (sx, sy, sz) != (1.0, 1.0, 1.0) or (rx, ry, rz) != (0.0, 0.0, 0.0) or (px, py, pz) != (0.0, 0.0, 0.0):
                out = np.array(a15, dtype=np.float32, copy=True)

                # scale position around pivot
                p = out[:, :3] - pivot[None, :]
                p[:, 0] *= sx
                p[:, 1] *= sy
                p[:, 2] *= sz

                # rotate position around pivot
                if (rx != 0.0) or (ry != 0.0) or (rz != 0.0):
                    # Match mesh convention: gl_view stores rot_deg with negated-angle convention.
                    qg = _quat_from_euler_deg(-rx, -ry, -rz)

                    # Vectorized rotate for p (N,3) using: v' = v + w*t + cross(q, t), t = 2*cross(q, v)
                    qx, qy, qz, qw = float(qg[0]), float(qg[1]), float(qg[2]), float(qg[3])
                    qv = np.array([qx, qy, qz], dtype=np.float32)

                    t = 2.0 * np.cross(qv[None, :], p)
                    p = p + (qw * t) + np.cross(qv[None, :], t)

                    # Vectorized quaternion multiply: q' = qg * qlocal
                    qlocal = out[:, 11:15]
                    bx = qlocal[:, 0]
                    by = qlocal[:, 1]
                    bz = qlocal[:, 2]
                    bw = qlocal[:, 3]

                    qlocal = np.stack(
                        [
                            (qw * bx + qx * bw + qy * bz - qz * by),
                            (qw * by - qx * bz + qy * bw + qz * bx),
                            (qw * bz + qx * by - qy * bx + qz * bw),
                            (qw * bw - qx * bx - qy * by - qz * bz),
                        ],
                        axis=1,
                    ).astype(np.float32, copy=False)

                    out[:, 11:15] = qlocal

                out[:, :3] = p + pivot[None, :] + np.array([px, py, pz], dtype=np.float32)[None, :]

                # scale the splat ellipsoid itself (scale3)
                out[:, 8] *= abs(sx)
                out[:, 9] *= abs(sy)
                out[:, 10] *= abs(sz)

                a15 = out

            # update bounds for picking (post-xform)
            try:
                mins = a15[:, :3].min(axis=0).astype("f4")
                maxs = a15[:, :3].max(axis=0).astype("f4")
                try:
                    bounds_by_owner[owner] = (mins, maxs)
                except Exception:
                    pass
            except Exception:
                pass

            arrays15.append(a15)
            owner_is_lit = False
            try:
                owner_norm = str(owner or "").strip().lower()
                if owner_norm and owner_norm in skinned_proxy_owners:
                    lit_splat_owners.add(owner_norm)
                    owner_is_lit = True
                else:
                    unlit_splat_owners.add(owner_norm)
            except Exception:
                unlit_splat_owners.add(str(owner or "").strip().lower())
            try:
                lit_arrays.append(
                    np.full((int(a15.shape[0]), 1), 1.0 if owner_is_lit else 0.0, dtype=np.float32)
                )
            except Exception:
                lit_arrays.append(np.zeros((int(a15.shape[0]), 1), dtype=np.float32))
            try:
                glow_values = None
                if isinstance(glow_by_owner, dict):
                    glow_values = glow_by_owner.get(owner)
                    if glow_values is None:
                        owner_key = str(owner or "").strip().lower()
                        for glow_owner, candidate in glow_by_owner.items():
                            if str(glow_owner or "").strip().lower() == owner_key:
                                glow_values = candidate
                                break
                glow_values = np.asarray(glow_values, dtype=np.float32).reshape(-1, 1)
                if int(glow_values.shape[0]) != int(a15.shape[0]):
                    glow_values = np.zeros((int(a15.shape[0]), 1), dtype=np.float32)
                glow_arrays.append(
                    np.clip(glow_values, np.float32(0.0), np.float32(4.0)).astype(np.float32, copy=False)
                )
            except Exception:
                glow_arrays.append(np.zeros((int(a15.shape[0]), 1), dtype=np.float32))
            try:
                splats_world[owner] = a15[:, :3].astype(np.float32, copy=True)
            except Exception:
                pass
            try:
                retarget_handles = getattr(self, "_mgl_retarget_joint_handles_by_owner", None)
                if isinstance(retarget_handles, dict):
                    matched_retarget_owner = False
                    owner_key = str(owner or "").strip().lower()
                    for retarget_owner in retarget_handles.keys():
                        if str(retarget_owner or "").strip().lower() == owner_key:
                            matched_retarget_owner = True
                            break
                    if matched_retarget_owner:
                        self._mgl_retarget_log(
                            "renderer_splat_world",
                            owner=str(owner),
                            xform={
                                "pos": [round(float(v), 6) for v in (px, py, pz)],
                                "rot": [round(float(v), 6) for v in (rx, ry, rz)],
                                "scl": [round(float(v), 6) for v in (sx, sy, sz)],
                            },
                            pivot=[round(float(v), 6) for v in np.asarray(pivot, dtype="f4").reshape(-1)[:3]],
                            local=self._mgl_retarget_points_summary(arr[:, :3]),
                            world=self._mgl_retarget_points_summary(a15[:, :3]),
                        )
            except Exception:
                pass

        if not arrays15:
            try:
                self._mgl_log_throttled(
                    "_mgl_splat_log_rebuild_empty_ts",
                    "splats: rebuild empty (no visible splats)",
                    1.0,
                )
            except Exception:
                pass
            self._mgl_disable_splats()
            return

        try:
            self._mgl_splats_has_lit = bool(lit_splat_owners)
            self._mgl_splats_all_lit = bool(lit_splat_owners) and not bool(unlit_splat_owners)
        except Exception:
            self._mgl_splats_has_lit = False
            self._mgl_splats_all_lit = False

        try:
            self._mgl_scene_splats_world = splats_world
        except Exception:
            pass

        combined = arrays15[0] if len(arrays15) == 1 else np.concatenate(arrays15, axis=0)
        combined_lit = lit_arrays[0] if len(lit_arrays) == 1 else np.concatenate(lit_arrays, axis=0)
        combined_glow = glow_arrays[0] if len(glow_arrays) == 1 else np.concatenate(glow_arrays, axis=0)
        try:
            if bool(getattr(self, "_mgl_splat_log_verbose", False)):
                self._mgl_log_throttled(
                    "_mgl_splat_log_combined_ts",
                    "splats: rebuild combined shape=" + str(getattr(combined, "shape", None)),
                    1.0,
                )
        except Exception:
            pass
        did_in_place = False
        try:
            # Fast path: same-size buffer, just overwrite bytes (no VAO/VBO rebuild)
            if combined is not None and getattr(combined, "ndim", 0) == 2 and int(combined.shape[1]) == 15:
                splats15 = combined.astype("f4", copy=False)
                splat_lit = combined_lit.astype("f4", copy=False)
                splat_glow = combined_glow.astype("f4", copy=False)

                vbo = getattr(self, "_mgl_splatq_vbo", None)
                lit_vbo = getattr(self, "_mgl_splatq_lit_vbo", None)
                glow_vbo = getattr(self, "_mgl_splatq_glow_vbo", None)
                if vbo is not None:
                    try:
                        vbo_size = int(getattr(vbo, "size", 0) or 0)
                    except Exception:
                        vbo_size = 0
                    try:
                        lit_vbo_size = int(getattr(lit_vbo, "size", 0) or 0) if lit_vbo is not None else 0
                    except Exception:
                        lit_vbo_size = 0
                    try:
                        glow_vbo_size = int(getattr(glow_vbo, "size", 0) or 0) if glow_vbo is not None else 0
                    except Exception:
                        glow_vbo_size = 0

                    if (
                        vbo_size == int(splats15.nbytes)
                        and lit_vbo_size == int(splat_lit.nbytes)
                        and glow_vbo_size == int(splat_glow.nbytes)
                    ):
                        # keep CPU copy (sorting path relies on this)
                        self._mgl_splat_count = int(splats15.shape[0])
                        self._mgl_splats15_cpu = splats15
                        self._mgl_splat_lit_cpu = splat_lit
                        self._mgl_splat_glow_cpu = splat_glow
                        try:
                            has_lit = bool(np.any(splat_lit > 0.5))
                            self._mgl_splats_has_lit = has_lit
                            self._mgl_splats_all_lit = has_lit and bool(np.all(splat_lit > 0.5))
                        except Exception:
                            pass

                        vbo.write(splats15.tobytes())
                        lit_vbo.write(splat_lit.tobytes())
                        glow_vbo.write(splat_glow.tobytes())
                        did_in_place = True
        except Exception:
            did_in_place = False

        # Fallback: count changed or buffer missing, do the full rebuild
        if not did_in_place:
            self.set_splats(combined, lit_flags=combined_lit, glow_flags=combined_glow)

        try:
            self._mgl_splats_need_rebuild = False
        except Exception:
            pass
        try:
            self._mgl_shadow_dirty = True
        except Exception:
            pass

        if state is not None:
            # Don't override an already-queued snapshot camera state.
            try:
                if getattr(self, "_mgl_pending_cam_state", None) is None:
                    self._mgl_queue_camera_state(state)
            except Exception:
                self._mgl_queue_camera_state(state)


    def _mgl_load_grid_model(self, path: Path, in_paint: bool = False) -> None:
        if not _HAS_MGL or self._mgl_ctx is None:
            self._mgl_error = "ModernGL context not ready"
            return
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            self._mgl_error = "Scene assembly not ready"
            return
        if self._mgl_grid_prog is None:
            self._mgl_error = "ModernGL grid program not ready"
            return
        try:
            points = self._mgl_load_obj_edge_vertices(path)
        except Exception as exc:
            self._mgl_error = f"Grid model load failed: {exc}"
            return
        if points is None or points.size == 0:
            self._mgl_error = "Grid model load failed: no edges"
            return
        points = points.astype("f4").reshape(-1, 3)
        did_make_current = False
        try:
            if not in_paint:
                self.makeCurrent()
                did_make_current = True
            self._mgl_clear_grid_model()
            vbo = self._mgl_ctx.buffer(points.tobytes())
            vao = self._mgl_ctx.simple_vertex_array(self._mgl_grid_prog, vbo, "in_position")
            self._mgl_grid_model_vao = vao
            self._mgl_grid_model_vbo = vbo
            self._mgl_grid_model_count = int(points.shape[0])
            scene.remove_by_tag("grid-model")
            item = MGLSceneItem(
                name="grid",
                draw_fn=MGLRendererMixin._mgl_draw_scene_grid,
                payload={
                    "vao": vao,
                    "color": (0.8, 0.8, 0.8),
                    "mode": moderngl.LINES,
                },
                resources=[vao, vbo],
                visible=bool(self._mgl_grid_model_visible),
                order=20,
                tag="grid-model",
            )
            scene.add(item)
            self._mgl_error = ""
        except Exception as exc:
            self._mgl_error = f"Grid model upload failed: {exc}"
            self._mgl_clear_grid_model()
        finally:
            if did_make_current:
                try:
                    self.doneCurrent()
                except Exception:
                    pass

    def _mgl_draw_scene_mesh(self, item: MGLSceneItem, mvp) -> None:
        if self._mgl_ctx is None or self._mgl_prog is None:
            return
        payload = item.payload or {}
        tag = str(getattr(item, "tag", "") or "")
        scene_skeleton_handles = tag == "scene-skeleton-handles"
        if isinstance(payload.get("fbx_rig_context"), dict):
            self._mgl_refresh_fbx_rig_mesh_item(item)
            payload = item.payload or {}
        self._mgl_refresh_music_effects_mesh_item(item)
        payload = item.payload or {}
        submeshes = payload.get("submeshes")
        mesh_entry = payload.get("mesh_entry") if isinstance(payload.get("mesh_entry"), dict) else None
        vao = payload.get("vao")
        if not submeshes and vao is None:
            if scene_skeleton_handles:
                self._mgl_scene_skeleton_log(
                    "draw_mesh_skip",
                    owner=str(payload.get("owner") or ""),
                    item=str(getattr(item, "name", "") or ""),
                    reason="no_geometry",
                )
            if payload.get("material") is not None:
                self._mgl_material_log(
                    "renderer.draw.skip_no_geometry",
                    owner=str(payload.get("owner") or ""),
                    path=str(payload.get("path") or ""),
                    material=payload.get("material"),
                )
            return
        try:
            instance_count = max(1, int(payload.get("instance_count", 1) or 1))
        except Exception:
            instance_count = 1
        use_instancing = bool(payload.get("gpu_instancing", False)) and instance_count > 0

        def _vao_for_entry(entry, fallback=None):
            if isinstance(entry, dict):
                if use_instancing:
                    instanced_vao = entry.get("instanced_vao")
                    if instanced_vao is not None:
                        return instanced_vao
                normal_vao = entry.get("vao")
                if normal_vao is not None:
                    return normal_vao
            return fallback

        def _render_vao(draw_vao) -> None:
            if draw_vao is None:
                return
            if use_instancing:
                draw_vao.render(instances=instance_count)
            else:
                draw_vao.render()

        def _render_entry(entry, fallback=None) -> None:
            _render_vao(_vao_for_entry(entry, fallback))

        edge_wire = bool(payload.get("edge_wire"))
        path_key = str(payload.get("path", "") or "").strip().lower()
        is_fbx_payload = path_key.endswith(".fbx")
        model_np = None
        try:
            payload = item.payload or {}

            mvp_to_use = mvp
            model = payload.get("model")
            if model is not None:
                try:
                    if Matrix44 is not None and isinstance(model, Matrix44):
                        mvp_to_use = mvp * model
                        if np is not None:
                            model_np = np.array(model, dtype="f4")
                    elif Matrix44 is not None:
                        # model may be a numpy 4x4; Matrix44 can build from it
                        mvp_to_use = mvp * Matrix44(model, dtype="f4")
                        if np is not None:
                            model_np = np.array(model, dtype="f4")
                    elif np is not None:
                        model_np = np.array(model, dtype="f4")
                except Exception:
                    mvp_to_use = mvp

            self._mgl_prog["Mvp"].write(mvp_to_use.astype("f4").tobytes())
            try:
                self._mgl_prog["UseInstancing"].value = 1 if use_instancing else 0
            except Exception:
                pass
            if np is not None:
                if model_np is None:
                    model_np = np.eye(4, dtype="f4")
                try:
                    self._mgl_prog["Model"].write(model_np.tobytes())
                except Exception:
                    pass
            try:
                self._mgl_prog["UseVolumeMask"].value = 0
            except Exception:
                pass
            try:
                self._mgl_apply_shadow_uniforms(self._mgl_prog)
            except Exception:
                pass
            try:
                shadow_id = max(0, min(65535, int(getattr(item, "item_id", 0) or 0))) / 65535.0
                self._mgl_prog["ShadowReceiverId"].value = float(shadow_id)
            except Exception:
                pass

        except Exception:
            pass
        proc_state = None
        try:
            owner = payload.get("owner")
        except Exception:
            owner = None
        if owner:
            try:
                entry = getattr(self, "_mgl_scene_proc_textures_by_owner", {}).get(owner)
            except Exception:
                entry = None
            if isinstance(entry, dict) and entry.get("gpu"):
                proc_state = entry.get("gpu_state")
        else:
            try:
                if bool(getattr(self, "_mgl_proc_gpu_enabled", False)):
                    proc_state = getattr(self, "_mgl_proc_gpu_state", None)
            except Exception:
                proc_state = None
        self._mgl_apply_procedural_uniforms(proc_state)
        manual_texture = self._mgl_texture if self._mgl_texture_override else None
        weight_debug = bool(payload.get("_fbx_skin_weight_debug", False))
        use_vertex_color = bool(weight_debug or payload.get("use_vertex_color", False))
        material = self._mgl_normalize_material(payload.get("material"))
        is_transparent_material = (not weight_debug) and self._mgl_material_is_transparent(material)
        if scene_skeleton_handles:
            self._mgl_scene_skeleton_log_throttled(
                f"draw_mesh_begin:{getattr(item, 'item_id', 0)}",
                "draw_mesh_begin",
                interval=1.0,
                owner=str(payload.get("owner") or ""),
                item=str(getattr(item, "name", "") or ""),
                visible=bool(getattr(item, "visible", False)),
                has_vao=bool(vao is not None),
                use_vertex_color=bool(use_vertex_color),
            )
        if material is not None:
            owner_key = str(owner or path_key or item.name or "scene-material")
            self._mgl_material_log_throttled(
                f"draw:{owner_key}",
                "renderer.draw.material",
                interval=1.0,
                owner=str(owner or ""),
                path=str(payload.get("path") or ""),
                visible=bool(getattr(item, "visible", False)),
                transparent=bool(is_transparent_material),
                transparency=float((material or {}).get("transparency", 0.0) or 0.0),
                ior=float((material or {}).get("ior", 1.0) or 1.0),
                tint_color=list((material or {}).get("tint_color", (1.0, 1.0, 1.0))),
                fresnel_amount=float((material or {}).get("fresnel_amount", 0.0) or 0.0),
                fresnel_color=list((material or {}).get("fresnel_color", (1.0, 1.0, 1.0))),
                submeshes=int(len(submeshes or [])),
                has_vao=bool(vao is not None),
            )
        def _apply_material_uniforms() -> None:
            if weight_debug:
                try:
                    self._mgl_prog["UseMaterial"].value = 0
                except Exception:
                    pass
                try:
                    self._mgl_prog["UseSceneRefraction"].value = 0
                except Exception:
                    pass
                return
            use_material = 1 if self._mgl_material_has_effect(material) else 0
            transparency = 0.0
            ior = 1.0
            tint = (1.0, 1.0, 1.0)
            fresnel_amount = 0.0
            fresnel_color = (1.0, 1.0, 1.0)
            use_scene_refraction = 0
            if isinstance(material, dict):
                transparency = float(material.get("transparency", 0.0) or 0.0)
                ior = float(material.get("ior", 1.0) or 1.0)
                tint = tuple(material.get("tint_color", (1.0, 1.0, 1.0)) or (1.0, 1.0, 1.0))
                fresnel_amount = float(material.get("fresnel_amount", 0.0) or 0.0)
                fresnel_color = tuple(material.get("fresnel_color", (1.0, 1.0, 1.0)) or (1.0, 1.0, 1.0))
                if ior > 1.001 and bool(getattr(self, "_mgl_material_scene_valid", False)):
                    scene_tex = getattr(self, "_mgl_material_scene_tex", None)
                    if scene_tex is not None:
                        try:
                            scene_tex.use(location=5)
                            use_scene_refraction = 1
                        except Exception:
                            use_scene_refraction = 0
            try:
                self._mgl_prog["UseMaterial"].value = use_material
            except Exception:
                pass
            try:
                self._mgl_prog["MaterialTransparency"].value = transparency
            except Exception:
                pass
            try:
                self._mgl_prog["MaterialIor"].value = ior
            except Exception:
                pass
            try:
                self._mgl_prog["MaterialTint"].value = tint
            except Exception:
                pass
            try:
                self._mgl_prog["MaterialFresnelAmount"].value = fresnel_amount
            except Exception:
                pass
            try:
                self._mgl_prog["MaterialFresnelColor"].value = fresnel_color
            except Exception:
                pass
            try:
                self._mgl_prog["UseSceneRefraction"].value = use_scene_refraction
            except Exception:
                pass
            try:
                screen_size = getattr(self, "_mgl_material_scene_screen_size", None) or self._mgl_render_size()
                self._mgl_prog["ScreenSize"].value = (float(screen_size[0]), float(screen_size[1]))
            except Exception:
                pass
            try:
                cam_world = getattr(self, "_mgl_cam_world", None)
                if isinstance(cam_world, (list, tuple)) and len(cam_world) >= 3:
                    self._mgl_prog["CameraWorldPos"].value = (
                        float(cam_world[0]),
                        float(cam_world[1]),
                        float(cam_world[2]),
                    )
                else:
                    self._mgl_prog["CameraWorldPos"].value = (0.0, 0.0, 4.0)
            except Exception:
                pass

        def _apply_weight_debug_uniforms() -> None:
            if not weight_debug:
                return
            try:
                self._mgl_prog["UseVertexColor"].value = 1
            except Exception:
                pass
            try:
                self._mgl_prog["UseProcedural"].value = 0
                self._mgl_prog["UseProceduralLayer"].value = 0
            except Exception:
                pass
            try:
                self._mgl_prog["UseTexture"].value = 0
            except Exception:
                pass
            try:
                self._mgl_prog["UseMaterial"].value = 0
                self._mgl_prog["UseSceneRefraction"].value = 0
            except Exception:
                pass

        _apply_material_uniforms()
        _apply_weight_debug_uniforms()
        if use_vertex_color:
            try:
                self._mgl_prog["UseVertexColor"].value = 1
                self._mgl_prog["UseTexture"].value = 0
            except Exception:
                pass
        else:
            try:
                self._mgl_prog["UseVertexColor"].value = 0
            except Exception:
                pass

        if scene_skeleton_handles and vao is not None:
            prev_depth_mask = None
            prev_depth_func = None
            prev_depth_test = True
            prev_wireframe = None
            try:
                prev_depth_test = bool(getattr(self._mgl_ctx, "depth_test", True))
            except Exception:
                prev_depth_test = True
            try:
                prev_depth_mask = getattr(self._mgl_ctx, "depth_mask", None)
            except Exception:
                prev_depth_mask = None
            try:
                prev_depth_func = getattr(self._mgl_ctx, "depth_func", None)
            except Exception:
                prev_depth_func = None
            try:
                prev_wireframe = bool(getattr(self._mgl_ctx, "wireframe", False))
            except Exception:
                prev_wireframe = False
            try:
                self._mgl_ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
            except Exception:
                pass
            try:
                self._mgl_ctx.depth_mask = False
            except Exception:
                pass
            try:
                self._mgl_ctx.wireframe = False
            except Exception:
                pass
            try:
                self._mgl_prog["UseTexture"].value = 0
                self._mgl_prog["UseVertexColor"].value = 1
                self._mgl_prog["UseLighting"].value = 0
                self._mgl_prog["UseMaterial"].value = 0
                self._mgl_prog["Color"].value = tuple(payload.get("color") or (0.72, 0.82, 0.96, 1.0))
                try:
                    self._mgl_prog["UseProcedural"].value = 0
                    self._mgl_prog["UseProceduralLayer"].value = 0
                except Exception:
                    pass
                _render_entry(mesh_entry, vao)
                self._mgl_scene_skeleton_log_throttled(
                    f"draw_mesh_done:{getattr(item, 'item_id', 0)}",
                    "draw_mesh_done",
                    interval=1.0,
                    owner=str(payload.get("owner") or ""),
                    item=str(getattr(item, "name", "") or ""),
                    depth="disabled",
                )
            except Exception as exc:
                self._mgl_error = f"Scene mesh draw failed: {exc}"
                self._mgl_scene_skeleton_log(
                    "draw_mesh_error",
                    owner=str(payload.get("owner") or ""),
                    item=str(getattr(item, "name", "") or ""),
                    error=repr(exc),
                )
            finally:
                try:
                    self._mgl_ctx.wireframe = prev_wireframe
                except Exception:
                    pass
                if prev_depth_mask is not None:
                    try:
                        self._mgl_ctx.depth_mask = prev_depth_mask
                    except Exception:
                        pass
                if prev_depth_func is not None:
                    try:
                        self._mgl_ctx.depth_func = prev_depth_func
                    except Exception:
                        pass
                if prev_depth_test is not None:
                    try:
                        if prev_depth_test:
                            self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                        else:
                            self._mgl_ctx.disable(moderngl.DEPTH_TEST)
                    except Exception:
                        pass
                try:
                    if bool(getattr(self, "_mgl_cull_enabled", False)):
                        self._mgl_ctx.enable(moderngl.CULL_FACE)
                    else:
                        self._mgl_ctx.disable(moderngl.CULL_FACE)
                except Exception:
                    pass
            return

        prev_depth_mask_material = None
        if is_transparent_material:
            try:
                prev_depth_mask_material = getattr(self._mgl_ctx, "depth_mask", None)
            except Exception:
                prev_depth_mask_material = None
            try:
                self._mgl_ctx.depth_mask = False
            except Exception:
                pass
        # For FBX, avoid fallback triangle-wire overlay; only show explicit edge wire items.
        wire_overlay = bool(
            self._mgl_wireframe
            and not edge_wire
            and (not is_fbx_payload)
            and (submeshes or vao is not None)
        )
        explicit_edge_wire = bool(
            self._mgl_wireframe
            and edge_wire
            and (submeshes or vao is not None)
        )

        def _render_depth_prepass(draw_geometry) -> None:
            prev_depth_mask = None
            prev_depth_func = None
            prev_wireframe = None
            raw_gl = getattr(self, "_gl", None)
            color_mask_disabled = False
            try:
                prev_depth_mask = getattr(self._mgl_ctx, "depth_mask", None)
            except Exception:
                prev_depth_mask = None
            try:
                prev_depth_func = getattr(self._mgl_ctx, "depth_func", None)
            except Exception:
                prev_depth_func = None
            try:
                prev_wireframe = bool(getattr(self._mgl_ctx, "wireframe", False))
            except Exception:
                prev_wireframe = False

            try:
                self._mgl_ctx.depth_mask = True
            except Exception:
                pass
            try:
                self._mgl_ctx.depth_func = "<="
            except Exception:
                pass
            try:
                self._mgl_ctx.wireframe = False
            except Exception:
                pass

            try:
                if raw_gl is not None and hasattr(raw_gl, "glColorMask"):
                    raw_gl.glColorMask(False, False, False, False)
                    color_mask_disabled = True
            except Exception:
                color_mask_disabled = False

            try:
                if color_mask_disabled:
                    # Stamp full mesh depth even if the visible material has alpha/discard.
                    self._mgl_apply_procedural_uniforms(None)
                    try:
                        self._mgl_prog["UseMaterial"].value = 0
                    except Exception:
                        pass
                    self._mgl_prog["UseTexture"].value = 0
                    self._mgl_prog["UseLighting"].value = 0
                    self._mgl_prog["Color"].value = (0.0, 0.0, 0.0, 1.0)
                    try:
                        self._mgl_prog["UseVertexColor"].value = 0
                    except Exception:
                        pass
                    draw_geometry()
            finally:
                if color_mask_disabled:
                    try:
                        raw_gl.glColorMask(True, True, True, True)
                    except Exception:
                        pass
                try:
                    self._mgl_ctx.wireframe = prev_wireframe
                except Exception:
                    pass
                if prev_depth_mask is not None:
                    try:
                        self._mgl_ctx.depth_mask = prev_depth_mask
                    except Exception:
                        pass
                if prev_depth_func is not None:
                    try:
                        self._mgl_ctx.depth_func = prev_depth_func
                    except Exception:
                        pass
                else:
                    try:
                        self._mgl_ctx.depth_func = "<="
                    except Exception:
                        pass
                try:
                    self._mgl_apply_procedural_uniforms(proc_state)
                except Exception:
                    pass
                try:
                    _apply_material_uniforms()
                except Exception:
                    pass
                try:
                    _apply_weight_debug_uniforms()
                except Exception:
                    pass

        def _render_wire_overlay(draw_geometry) -> None:
            prev_depth_mask = None
            prev_depth_func = None
            prev_wireframe = None
            prev_line_width = None
            restore_cull = not bool(getattr(self, "_mgl_cull_enabled", False))
            try:
                prev_depth_mask = getattr(self._mgl_ctx, "depth_mask", None)
            except Exception:
                prev_depth_mask = None
            try:
                prev_depth_func = getattr(self._mgl_ctx, "depth_func", None)
            except Exception:
                prev_depth_func = None
            try:
                prev_wireframe = bool(getattr(self._mgl_ctx, "wireframe", False))
            except Exception:
                prev_wireframe = False
            try:
                prev_line_width = float(getattr(self._mgl_ctx, "line_width", 1.0))
            except Exception:
                prev_line_width = None

            try:
                _render_depth_prepass(draw_geometry)
            except Exception:
                pass

            try:
                self._mgl_ctx.depth_mask = False
            except Exception:
                pass
            try:
                self._mgl_ctx.depth_func = "<="
            except Exception:
                pass
            if restore_cull:
                try:
                    self._mgl_ctx.enable(moderngl.CULL_FACE)
                except Exception:
                    pass
            try:
                self._mgl_ctx.line_width = float(self._mgl_wire_line_width)
            except Exception:
                pass
            try:
                self._mgl_ctx.wireframe = True
            except Exception:
                pass
            try:
                self._mgl_prog["Color"].value = self._mgl_wire_color
                self._mgl_prog["UseMaterial"].value = 0
                self._mgl_prog["UseVertexColor"].value = 0
            except Exception:
                pass
            try:
                draw_geometry()
            finally:
                try:
                    self._mgl_ctx.wireframe = prev_wireframe
                except Exception:
                    pass
                if prev_line_width is not None:
                    try:
                        self._mgl_ctx.line_width = prev_line_width
                    except Exception:
                        pass
                if prev_depth_mask is not None:
                    try:
                        self._mgl_ctx.depth_mask = prev_depth_mask
                    except Exception:
                        pass
                if prev_depth_func is not None:
                    try:
                        self._mgl_ctx.depth_func = prev_depth_func
                    except Exception:
                        pass
                else:
                    try:
                        self._mgl_ctx.depth_func = "<="
                    except Exception:
                        pass
                if restore_cull:
                    try:
                        self._mgl_ctx.disable(moderngl.CULL_FACE)
                    except Exception:
                        pass
                try:
                    self._mgl_apply_procedural_uniforms(proc_state)
                except Exception:
                    pass
                try:
                    _apply_material_uniforms()
                except Exception:
                    pass
                try:
                    _apply_weight_debug_uniforms()
                except Exception:
                    pass

        if wire_overlay:
            try:
                self._mgl_ctx.polygon_offset = (1.0, 1.0)
            except Exception:
                pass

        if submeshes:
            if explicit_edge_wire:
                def _draw_submeshes_depth() -> None:
                    for sub in submeshes:
                        _render_entry(sub)

                _render_depth_prepass(_draw_submeshes_depth)
                self._mgl_apply_procedural_uniforms(proc_state)
                _apply_material_uniforms()
            for sub in submeshes:
                color = sub.get("color") or self._mgl_mesh_color
                tex = manual_texture or sub.get("texture")
                use_texture = (tex is not None) and (not use_vertex_color)
                try:
                    self._mgl_prog["UseTexture"].value = 1 if use_texture else 0
                    self._mgl_prog["UseVertexColor"].value = 1 if use_vertex_color else 0
                    self._mgl_prog["UseLighting"].value = 1
                    self._mgl_prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
                    self._mgl_prog["Color"].value = color
                except Exception:
                    pass
                if use_texture:
                    try:
                        tex.use(location=0)
                    except Exception:
                        pass
                _render_entry(sub)
            if wire_overlay:
                try:
                    self._mgl_ctx.polygon_offset = (0.0, 0.0)
                except Exception:
                    pass
                def _draw_submeshes() -> None:
                    for sub in submeshes:
                        _render_entry(sub)

                _render_wire_overlay(_draw_submeshes)
        else:
            if explicit_edge_wire and vao is not None:
                _render_depth_prepass(lambda: _render_entry(mesh_entry, vao))
                self._mgl_apply_procedural_uniforms(proc_state)
                _apply_material_uniforms()
            color = payload.get("color") or self._mgl_mesh_color
            tex = manual_texture or payload.get("texture")
            use_texture = (tex is not None) and (not use_vertex_color)
            try:
                self._mgl_prog["UseTexture"].value = 1 if use_texture else 0
                self._mgl_prog["UseVertexColor"].value = 1 if use_vertex_color else 0
                self._mgl_prog["UseLighting"].value = 1
                self._mgl_prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
                self._mgl_prog["Color"].value = color
            except Exception:
                pass
            if use_texture:
                try:
                    tex.use(location=0)
                except Exception:
                    pass
            if vao is not None:
                _render_entry(mesh_entry, vao)
            if wire_overlay and vao is not None:
                try:
                    self._mgl_ctx.polygon_offset = (0.0, 0.0)
                except Exception:
                    pass
                _render_wire_overlay(lambda: _render_entry(mesh_entry, vao))

        overrides = None
        if owner:
            try:
                overrides = getattr(self, "_mgl_scene_volume_overrides_by_owner", {}).get(owner)
            except Exception:
                overrides = None
        if overrides:

            def _draw_override(tex_override):
                if submeshes:
                    for sub in submeshes:
                        color = sub.get("color") or self._mgl_mesh_color
                        tex = tex_override
                        use_texture = (tex is not None) and (not use_vertex_color)
                        try:
                            self._mgl_prog["UseTexture"].value = 1 if use_texture else 0
                            self._mgl_prog["UseVertexColor"].value = 1 if use_vertex_color else 0
                            self._mgl_prog["UseLighting"].value = 1
                            self._mgl_prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
                            self._mgl_prog["Color"].value = color
                        except Exception:
                            pass
                        if use_texture:
                            try:
                                tex.use(location=0)
                            except Exception:
                                pass
                        _render_entry(sub)
                else:
                    color = payload.get("color") or self._mgl_mesh_color
                    tex = tex_override
                    use_texture = (tex is not None) and (not use_vertex_color)
                    try:
                        self._mgl_prog["UseTexture"].value = 1 if use_texture else 0
                        self._mgl_prog["UseVertexColor"].value = 1 if use_vertex_color else 0
                        self._mgl_prog["UseLighting"].value = 1
                        self._mgl_prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
                        self._mgl_prog["Color"].value = color
                    except Exception:
                        pass
                    if use_texture:
                        try:
                            tex.use(location=0)
                        except Exception:
                            pass
                    if vao is not None:
                        _render_entry(mesh_entry, vao)

            def _volume_inv_matrix(vol_owner: str):
                if np is None or not vol_owner:
                    return None
                bounds_map = getattr(self, "_mgl_scene_mesh_bounds_by_owner", None) or getattr(
                    self, "_mgl_scene_bounds_by_owner", None
                )
                b = None
                if isinstance(bounds_map, dict):
                    b = bounds_map.get(vol_owner)
                    if b is None:
                        lk = vol_owner.lower()
                        for k, v in bounds_map.items():
                            try:
                                if str(k).strip().lower() == lk:
                                    b = v
                                    break
                            except Exception:
                                continue

                found = False
                model = None
                scene = getattr(self, "_mgl_scene", None)
                if scene is not None:
                    try:
                        for item in scene.iter_by_tag("scene-volume"):
                            payload_i = getattr(item, "payload", None) or {}
                            if payload_i.get("owner") == vol_owner:
                                found = True
                                model = payload_i.get("model")
                                break
                    except Exception:
                        pass
                if b is not None:
                    found = True
                if not found:
                    return None

                if model is None:
                    if b is None:
                        return np.eye(4, dtype="f4")
                    try:
                        bmin, bmax = b
                        bmin = np.array(bmin, dtype=np.float32)
                        bmax = np.array(bmax, dtype=np.float32)
                        x = self._mgl_get_scene_asset_xform(vol_owner)
                        px, py, pz = x.get("pos", (0.0, 0.0, 0.0))
                        rx, ry, rz = x.get("rot", (0.0, 0.0, 0.0))
                        sx, sy, sz = x.get("scl", (1.0, 1.0, 1.0))
                        cx, cy, cz = self._mgl_owner_pivot_local(vol_owner, bmin, bmax)

                        def T(tx, ty, tz):
                            m = np.eye(4, dtype=np.float32)
                            m[3, 0] = tx
                            m[3, 1] = ty
                            m[3, 2] = tz
                            return m

                        def S(sxv, syv, szv):
                            m = np.eye(4, dtype=np.float32)
                            m[0, 0] = sxv
                            m[1, 1] = syv
                            m[2, 2] = szv
                            return m

                        def Rx(a):
                            a = math.radians(a)
                            c_, s_ = math.cos(a), math.sin(a)
                            m = np.eye(4, dtype=np.float32)
                            m[1, 1] = c_
                            m[1, 2] = s_
                            m[2, 1] = -s_
                            m[2, 2] = c_
                            return m

                        def Ry(a):
                            a = math.radians(a)
                            c_, s_ = math.cos(a), math.sin(a)
                            m = np.eye(4, dtype=np.float32)
                            m[0, 0] = c_
                            m[0, 2] = -s_
                            m[2, 0] = s_
                            m[2, 2] = c_
                            return m

                        def Rz(a):
                            a = math.radians(a)
                            c_, s_ = math.cos(a), math.sin(a)
                            m = np.eye(4, dtype=np.float32)
                            m[0, 0] = c_
                            m[0, 1] = s_
                            m[1, 0] = -s_
                            m[1, 1] = c_
                            return m

                        rot_rx, rot_ry, rot_rz = -float(rx), -float(ry), -float(rz)
                        xform_space = str(getattr(self, "_mgl_xform_space", "world") or "world").lower()
                        if xform_space == "local":
                            model = T(-cx, -cy, -cz) @ S(sx, sy, sz) @ (Rx(rot_rx) @ Ry(rot_ry) @ Rz(rot_rz)) @ T(
                                px, py, pz
                            )
                        else:
                            model = T(-cx, -cy, -cz) @ (Rx(rot_rx) @ Ry(rot_ry) @ Rz(rot_rz)) @ S(
                                sx, sy, sz
                            ) @ T(px, py, pz)
                    except Exception:
                        model = None

                if model is None:
                    return None
                try:
                    if Matrix44 is not None and isinstance(model, Matrix44):
                        model_np_local = np.array(model, dtype="f4")
                    else:
                        model_np_local = np.array(model, dtype="f4")
                except Exception:
                    return None
                try:
                    return np.linalg.inv(model_np_local)
                except Exception:
                    return None

            prev_offset = None
            try:
                prev_offset = self._mgl_ctx.polygon_offset
            except Exception:
                prev_offset = None
            try:
                self._mgl_ctx.polygon_offset = (-1.0, -1.0)
            except Exception:
                pass
            prev_wire = None
            try:
                prev_wire = bool(getattr(self._mgl_ctx, "wireframe", False))
                if prev_wire:
                    self._mgl_ctx.wireframe = False
            except Exception:
                prev_wire = None

            for override in overrides:
                vol_owner = str(override.get("volume_owner") or "").strip()
                inv = _volume_inv_matrix(vol_owner)
                if inv is None:
                    continue
                try:
                    self._mgl_prog["UseVolumeMask"].value = 1
                    self._mgl_prog["VolumeInv"].write(inv.astype("f4").tobytes())
                except Exception:
                    pass
                if override.get("gpu"):
                    self._mgl_apply_procedural_uniforms(override.get("gpu_state"))
                    tex_override = None
                else:
                    tex_override = override.get("texture")
                    self._mgl_apply_procedural_uniforms(None)
                _draw_override(tex_override)

            try:
                self._mgl_prog["UseVolumeMask"].value = 0
            except Exception:
                pass
            if prev_offset is not None:
                try:
                    self._mgl_ctx.polygon_offset = prev_offset
                except Exception:
                    pass
            else:
                try:
                    self._mgl_ctx.polygon_offset = (0.0, 0.0)
                except Exception:
                    pass
            if prev_wire is not None:
                try:
                    self._mgl_ctx.wireframe = prev_wire
                except Exception:
                    pass
        try:
            self._mgl_prog["UseMaterial"].value = 0
        except Exception:
            pass
        try:
            self._mgl_prog["UseSceneRefraction"].value = 0
        except Exception:
            pass
        if prev_depth_mask_material is not None:
            try:
                self._mgl_ctx.depth_mask = prev_depth_mask_material
            except Exception:
                pass

    def _mgl_draw_scene_fx_trail(self, item: MGLSceneItem, mvp) -> None:
        if self._mgl_ctx is None:
            return
        try:
            if not self._mgl_fx_update_trail_item(item):
                return
        except Exception as exc:
            self._mgl_fx_log(f"[renderer] draw update-exception owner={item.name} err={exc!r}")
            return
        payload = item.payload or {}
        mesh_cache = payload.get("_fx_instance_mesh")
        mesh_models = list(payload.get("_fx_instance_models") or [])
        if isinstance(mesh_cache, dict) and mesh_models:
            base_payload = dict(mesh_cache)
            base_payload["owner"] = str(payload.get("owner") or payload.get("target_owner") or "")
            base_payload["material"] = payload.get("instance_material") or payload.get("material")
            tex_override = payload.get("_fx_instance_texture")
            if tex_override is not None:
                submeshes = list(base_payload.get("submeshes") or [])
                if submeshes:
                    base_payload["submeshes"] = [{**sub, "texture": tex_override} for sub in submeshes]
                else:
                    base_payload["texture"] = tex_override
            try:
                for model in mesh_models:
                    draw_item = MGLSceneItem(
                        name=item.name,
                        draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                        payload={**base_payload, "model": model},
                        resources=[],
                        visible=item.visible,
                        order=item.order,
                        tag=item.tag,
                    )
                    self._mgl_draw_scene_mesh(draw_item, mvp)
            except Exception as exc:
                self._mgl_fx_log(f"[renderer] draw mesh-exception owner={item.name} err={exc!r}")
            return
        if self._mgl_wire_prog is None:
            self._mgl_fx_log_throttled(
                "draw-context-missing",
                "[renderer] draw skip: wire context unavailable",
                interval=3.0,
            )
            return
        try:
            self._mgl_draw_scene_wire(item, mvp)
        except Exception as exc:
            self._mgl_fx_log(f"[renderer] draw wire-exception owner={item.name} err={exc!r}")
            return

    def _mgl_draw_scene_grid(self, item: MGLSceneItem, mvp) -> None:
        if self._mgl_grid_prog is None:
            return
        payload = item.payload or {}
        vao = payload.get("vao")
        if vao is None:
            return
        prev_wireframe = False
        prev_line_width = None
        try:
            prev_wireframe = bool(getattr(self._mgl_ctx, "wireframe", False))
        except Exception:
            prev_wireframe = False
        try:
            prev_line_width = getattr(self._mgl_ctx, "line_width")
        except Exception:
            prev_line_width = None
        base_color = payload.get("color")
        if base_color is None:
            color = (0.8, 0.8, 0.8, float(self._mgl_grid_alpha))
        else:
            try:
                color = (
                    float(base_color[0]),
                    float(base_color[1]),
                    float(base_color[2]),
                    float(getattr(self, "_mgl_grid_alpha", 0.10)),
                )
            except Exception:
                color = (0.8, 0.8, 0.8, float(self._mgl_grid_alpha))
        try:
            self._mgl_grid_prog["Mvp"].write(mvp.astype("f4").tobytes())
            try:
                self._mgl_apply_shadow_uniforms(self._mgl_grid_prog)
            except Exception:
                pass
            self._mgl_grid_prog["Color"].value = color
            try:
                self._mgl_grid_prog["GridOffset"].value = (0.0, 0.0)
                self._mgl_grid_prog["FadeOrigin"].value = (0.0, 0.0)
                self._mgl_grid_prog["FadeStart"].value = 0.0
                self._mgl_grid_prog["FadeEnd"].value = 0.0
                self._mgl_grid_prog["MajorBoost"].value = 1.0
                self._mgl_grid_prog["MajorStep"].value = 1.0
                self._mgl_grid_prog["GridSpacing"].value = 1.0
            except Exception:
                pass
        except Exception:
            pass
        try:
            # Grid should be thin, independent of wireframe settings
            try:
                self._mgl_ctx.wireframe = False
            except Exception:
                pass
            try:
                self._mgl_ctx.line_width = 1.0
            except Exception:
                pass

            mode = payload.get("mode")
            if mode is None:
                vao.render()
            else:
                vao.render(mode)

        except Exception as exc:
            self._mgl_error = f"Scene grid draw failed: {exc}"
        finally:
            try:
                self._mgl_ctx.wireframe = prev_wireframe
            except Exception:
                pass
            if prev_line_width is not None:
                try:
                    self._mgl_ctx.line_width = prev_line_width
                except Exception:
                    pass

    def _mgl_draw_scene_wire(self, item: MGLSceneItem, mvp) -> None:
        if self._mgl_wire_prog is None:
            return
        payload = item.payload or {}
        tag = str(getattr(item, "tag", "") or "")
        scene_skeleton_overlay = bool(tag == "scene-rig-joints" and payload.get("scene_skeleton_overlay", False))
        if scene_skeleton_overlay:
            self._mgl_scene_skeleton_log_throttled(
                f"draw_wire_begin:{getattr(item, 'item_id', 0)}",
                "draw_wire_begin",
                interval=1.0,
                owner=str(payload.get("owner") or ""),
                item=str(getattr(item, "name", "") or ""),
                visible=bool(getattr(item, "visible", False)),
                has_vao=bool(payload.get("vao") is not None),
                segments=int(payload.get("segment_count", 0) or 0),
            )
        if tag == "scene-rig-joints":
            self._mgl_fbx_joints_log_throttled(
                f"_mgl_fbx_draw_begin_{getattr(item, 'item_id', 0)}",
                "draw begin "
                + f"item={item.name} id={getattr(item, 'item_id', 0)} visible={bool(getattr(item, 'visible', False))} "
                + f"has_context={bool(isinstance(payload.get('fbx_rig_context'), dict))} "
                + f"mode={payload.get('fbx_rig_pose_mode', '')} segments={int(payload.get('segment_count', 0) or 0)} "
                + f"owner={payload.get('owner', '')} path={payload.get('path', '')}",
                interval=0.35,
            )
        if isinstance(payload.get("fbx_rig_context"), dict):
            self._mgl_refresh_fbx_rig_wire_item(item)
            payload = item.payload or {}
        vao = payload.get("vao")
        if vao is None:
            if scene_skeleton_overlay:
                self._mgl_scene_skeleton_log("draw_wire_skip", owner=str(payload.get("owner") or ""), reason="no_vao")
            if tag == "scene-rig-joints":
                self._mgl_fbx_joints_log(
                    "draw skip reason=no_vao "
                    + f"item={item.name} id={getattr(item, 'item_id', 0)} "
                    + f"mode={payload.get('fbx_rig_pose_mode', '')} "
                    + f"segments={int(payload.get('segment_count', 0) or 0)}"
                )
            return
        is_volume = tag == "scene-volume"
        xray = bool(payload.get("xray", False))

        def _as_rgba(col):
            try:
                if len(col) == 4:
                    return tuple(float(c) for c in col)
                if len(col) == 3:
                    return (float(col[0]), float(col[1]), float(col[2]), 1.0)
            except Exception:
                pass
            return (0.25, 0.25, 0.25, 1.0)

        def _apply_uniforms(color_rgba):
            try:
                mvp_to_use = mvp
                model = payload.get("model")
                if tag == "scene-rig-joints" and bool(payload.get("ignore_owner_model", False)):
                    model = None
                if model is not None and Matrix44 is not None:
                    try:
                        if isinstance(model, Matrix44):
                            mvp_to_use = mvp * model
                        else:
                            mvp_to_use = mvp * Matrix44(model, dtype="f4")
                    except Exception:
                        mvp_to_use = mvp
                self._mgl_wire_prog["Mvp"].write(mvp_to_use.astype("f4").tobytes())
                self._mgl_wire_prog["Color"].value = color_rgba
                self._mgl_wire_prog["Viewport"].value = (float(max(1, self.width())), float(max(1, self.height())))
                self._mgl_wire_prog["LineWidth"].value = float(
                    payload.get("line_width", getattr(self, "_mgl_wire_edge_width", getattr(self, "_mgl_wire_line_width", 1.0)))
                )
            except Exception:
                pass

        def _render():
            mode = payload.get("mode")
            if mode is None:
                vao.render()
            else:
                vao.render(mode)

        prev_depth_test = None
        prev_depth_func = None
        prev_depth_mask = None
        try:
            prev_depth_test = bool(getattr(self._mgl_ctx, "depth_test", True))
        except Exception:
            prev_depth_test = True
        try:
            prev_depth_func = getattr(self._mgl_ctx, "depth_func", None)
        except Exception:
            prev_depth_func = None
        try:
            prev_depth_mask = getattr(self._mgl_ctx, "depth_mask", None)
        except Exception:
            prev_depth_mask = None

        try:
            self._mgl_ctx.enable(moderngl.DEPTH_TEST)
        except Exception:
            pass
        try:
            self._mgl_ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        except Exception:
            pass

        if scene_skeleton_overlay:
            try:
                self._mgl_ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
            except Exception:
                pass
            try:
                self._mgl_ctx.depth_mask = False
            except Exception:
                pass
            color = _as_rgba(payload.get("color") or self._mgl_wire_color)
            _apply_uniforms(color)
            try:
                _render()
                self._mgl_scene_skeleton_log_throttled(
                    f"draw_wire_done:{getattr(item, 'item_id', 0)}",
                    "draw_wire_done",
                    interval=1.0,
                    owner=str(payload.get("owner") or ""),
                    item=str(getattr(item, "name", "") or ""),
                    depth="disabled",
                )
            except Exception as exc:
                self._mgl_error = f"Scene wire draw failed: {exc}"
                self._mgl_scene_skeleton_log(
                    "draw_wire_error",
                    owner=str(payload.get("owner") or ""),
                    item=str(getattr(item, "name", "") or ""),
                    error=repr(exc),
                )
            finally:
                if prev_depth_mask is not None:
                    try:
                        self._mgl_ctx.depth_mask = prev_depth_mask
                    except Exception:
                        pass
                if prev_depth_func is not None:
                    try:
                        self._mgl_ctx.depth_func = prev_depth_func
                    except Exception:
                        pass
                try:
                    if prev_depth_test:
                        self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                    else:
                        self._mgl_ctx.disable(moderngl.DEPTH_TEST)
                except Exception:
                    pass
                try:
                    if bool(getattr(self, "_mgl_cull_enabled", False)):
                        self._mgl_ctx.enable(moderngl.CULL_FACE)
                    else:
                        self._mgl_ctx.disable(moderngl.CULL_FACE)
                except Exception:
                    pass
            return

        if not is_volume and not xray:
            try:
                self._mgl_ctx.depth_func = "<="
            except Exception:
                pass
            try:
                self._mgl_ctx.depth_mask = False
            except Exception:
                pass
            color = _as_rgba(payload.get("color") or self._mgl_wire_color)
            _apply_uniforms(color)
            try:
                _render()
            except Exception as exc:
                self._mgl_error = f"Scene wire draw failed: {exc}"
                if tag == "scene-rig-joints":
                    self._mgl_fbx_joints_log(
                        "draw error "
                        + f"item={item.name} id={getattr(item, 'item_id', 0)} "
                        + f"pass=front err={exc!r}"
                    )
            finally:
                if prev_depth_mask is not None:
                    try:
                        self._mgl_ctx.depth_mask = prev_depth_mask
                    except Exception:
                        pass
                if prev_depth_func is not None:
                    try:
                        self._mgl_ctx.depth_func = prev_depth_func
                    except Exception:
                        pass
                if prev_depth_test is not None:
                    try:
                        if prev_depth_test:
                            self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                        else:
                            self._mgl_ctx.disable(moderngl.DEPTH_TEST)
                    except Exception:
                        pass
            return

        try:
            self._mgl_ctx.depth_mask = False
        except Exception:
            pass

        base_color = _as_rgba(payload.get("color") or self._mgl_wire_color)
        front_color = base_color
        try:
            back_alpha_scale = float(payload.get("xray_back_alpha", 0.25) or 0.25)
        except Exception:
            back_alpha_scale = 0.25
        back_alpha_scale = max(0.0, min(1.0, back_alpha_scale))
        back_color = (base_color[0], base_color[1], base_color[2], base_color[3] * back_alpha_scale)

        try:
            try:
                self._mgl_ctx.depth_func = "<="
            except Exception:
                pass
            _apply_uniforms(front_color)
            _render()

            try:
                self._mgl_ctx.depth_func = ">"
            except Exception:
                pass
            _apply_uniforms(back_color)
            _render()
        except Exception as exc:
            self._mgl_error = f"Scene wire draw failed: {exc}"
            if tag == "scene-rig-joints":
                self._mgl_fbx_joints_log(
                    "draw error "
                    + f"item={item.name} id={getattr(item, 'item_id', 0)} "
                    + f"pass=xray err={exc!r}"
                )
        finally:
            if prev_depth_mask is not None:
                try:
                    self._mgl_ctx.depth_mask = prev_depth_mask
                except Exception:
                    pass
            if prev_depth_func is not None:
                try:
                    self._mgl_ctx.depth_func = prev_depth_func
                except Exception:
                    pass
            if prev_depth_test is not None:
                try:
                    if prev_depth_test:
                        self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                    else:
                        self._mgl_ctx.disable(moderngl.DEPTH_TEST)
                except Exception:
                    pass

    def _mgl_get_default_instance_buffers(self):
        if self._mgl_ctx is None or np is None:
            return None
        buffers = getattr(self, "_mgl_default_mesh_instance_buffers", None)
        if isinstance(buffers, tuple) and len(buffers) == 4 and all(buf is not None for buf in buffers):
            return buffers
        identity_cols = np.asarray(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype="f4",
        )
        try:
            buffers = tuple(self._mgl_ctx.buffer(identity_cols[idx : idx + 1].tobytes()) for idx in range(4))
        except Exception:
            return None
        self._mgl_default_mesh_instance_buffers = buffers
        return buffers

    def _mgl_mesh_vao_content(self, pos_buf, norm_buf, uv_buf, color_buf, instance_buffers=None):
        content = [
            (pos_buf, "3f", "in_position"),
            (norm_buf, "3f", "in_normal"),
            (uv_buf, "2f", "in_uv"),
            (color_buf, "4f", "in_color"),
        ]
        buffers = instance_buffers or self._mgl_get_default_instance_buffers()
        if isinstance(buffers, tuple) and len(buffers) == 4:
            content.extend(
                [
                    (buffers[0], "4f/i", "in_instance_col0"),
                    (buffers[1], "4f/i", "in_instance_col1"),
                    (buffers[2], "4f/i", "in_instance_col2"),
                    (buffers[3], "4f/i", "in_instance_col3"),
                ]
            )
        return content

    def _mgl_shadow_vao_content(self, pos_buf, instance_buffers=None):
        content = [(pos_buf, "3f", "in_position")]
        buffers = instance_buffers or self._mgl_get_default_instance_buffers()
        if isinstance(buffers, tuple) and len(buffers) == 4:
            content.extend(
                [
                    (buffers[0], "4f/i", "in_instance_col0"),
                    (buffers[1], "4f/i", "in_instance_col1"),
                    (buffers[2], "4f/i", "in_instance_col2"),
                    (buffers[3], "4f/i", "in_instance_col3"),
                ]
            )
        return content

    def _mgl_build_mesh_entry(
        self,
        points: NDArray,
        normals: NDArray,
        uvs: Optional[NDArray] = None,
        colors: Optional[NDArray] = None,
        indices: Optional[NDArray] = None,
    ) -> Optional[Dict[str, object]]:
        if not _HAS_MGL or self._mgl_ctx is None or self._mgl_prog is None:
            return None
        if points is None or points.size == 0:
            return None
        points = points.astype("f4").reshape(-1, 3)
        normals = normals.astype("f4").reshape(-1, 3)
        if uvs is None or uvs.size == 0:
            uvs = np.zeros((points.shape[0], 2), dtype="f4")
        uvs = uvs.astype("f4").reshape(-1, 2)
        if colors is None or colors.size == 0:
            colors = np.ones((points.shape[0], 4), dtype="f4")
        colors = np.asarray(colors, dtype="f4")
        if colors.ndim == 1:
            colors = colors.reshape(-1, 4)
        if colors.shape[1] == 3:
            alpha = np.ones((colors.shape[0], 1), dtype="f4")
            colors = np.concatenate((colors, alpha), axis=1)
        elif colors.shape[1] > 4:
            colors = colors[:, :4]
        if colors.shape[0] != points.shape[0]:
            if colors.shape[0] > points.shape[0]:
                colors = colors[: points.shape[0], :]
            else:
                pad = np.ones((points.shape[0] - colors.shape[0], 4), dtype="f4")
                colors = np.concatenate((colors, pad), axis=0)
        if indices is None:
            indices = np.arange(points.shape[0], dtype="u4")
        else:
            indices = np.asarray(indices, dtype="u4").ravel()
        index_buffer = self._mgl_ctx.buffer(indices.tobytes())
        pos_buf = self._mgl_ctx.buffer(points.tobytes())
        norm_buf = self._mgl_ctx.buffer(normals.tobytes())
        uv_buf = self._mgl_ctx.buffer(uvs.tobytes())
        color_buf = self._mgl_ctx.buffer(colors.tobytes())
        vao_content = self._mgl_mesh_vao_content(pos_buf, norm_buf, uv_buf, color_buf)
        vao = self._mgl_ctx.vertex_array(self._mgl_prog, vao_content, index_buffer, 4)
        return {
            "vao": vao,
            "vbo": pos_buf,
            "nbo": norm_buf,
            "tbo": uv_buf,
            "cbo": color_buf,
            "ibo": index_buffer,
            "count": int(indices.size),
            "uvs": uvs,
            "colors": colors,
            "points": points,
            "normals": normals,
        }

    def _mgl_build_submesh_entries(
        self,
        submeshes: List[SubMeshData],
    ) -> Tuple[List[Dict[str, object]], List[NDArray], List[str], int]:
        entries: List[Dict[str, object]] = []
        combined_uvs: List[NDArray] = []
        texture_paths: List[str] = []
        total_indices = 0
        if self._mgl_ctx is None or self._mgl_prog is None:
            return entries, combined_uvs, texture_paths, total_indices
        for sub in submeshes:
            points = sub.points.astype("f4").reshape(-1, 3)
            normals = sub.normals.astype("f4").reshape(-1, 3)
            uvs = sub.uvs.astype("f4").reshape(-1, 2)
            indices = np.arange(points.shape[0], dtype="u4")
            ibo = self._mgl_ctx.buffer(indices.tobytes())
            vbo = self._mgl_ctx.buffer(points.tobytes())
            nbo = self._mgl_ctx.buffer(normals.tobytes())
            tbo = self._mgl_ctx.buffer(uvs.tobytes())
            colors = np.ones((points.shape[0], 4), dtype="f4")
            cbo = self._mgl_ctx.buffer(colors.tobytes())
            vao_content = self._mgl_mesh_vao_content(vbo, nbo, tbo, cbo)
            vao = self._mgl_ctx.vertex_array(self._mgl_prog, vao_content, ibo, 4)

            texture = None
            if not self._mgl_texture_override:
                if sub.texture_path is not None and sub.texture_path.exists():
                    qimg = QtGui.QImage(str(sub.texture_path))
                    if not qimg.isNull():
                        if hasattr(QtGui.QImage, "Format_RGBA8888"):
                            qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                        else:
                            qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                        qimg = qimg.mirrored(False, True)
                        texture = self._mgl_make_texture(qimg)
                        texture_paths.append(str(sub.texture_path))
                elif sub.texture_image is not None:
                    qimg = self._mgl_qimage_from_texture(sub.texture_image)
                    if qimg is not None:
                        if hasattr(QtGui.QImage, "Format_RGBA8888"):
                            qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                        else:
                            qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                        qimg = qimg.mirrored(False, True)
                        texture = self._mgl_make_texture(qimg)
                        texture_paths.append("embedded")

            color = sub.base_color if sub.base_color is not None else self._mgl_mesh_color
            entries.append(
                {
                    "vao": vao,
                    "vbo": vbo,
                    "nbo": nbo,
                    "tbo": tbo,
                    "cbo": cbo,
                    "ibo": ibo,
                    "texture": texture,
                    "color": color,
                    "count": int(indices.size),
                    "points": points,
                    "normals": normals,
                    "uvs": uvs,
                    "colors": colors,
                    "name": str(getattr(sub, "name", "") or "").strip(),
                }
            )
            total_indices += int(indices.size)
            combined_uvs.append(uvs)
        return entries, combined_uvs, texture_paths, total_indices

    def _mgl_copy_to_points_instance_matrices(self, cfg: dict):
        if np is None or not isinstance(cfg, dict):
            return None
        if not bool(cfg.get("gpu_instances", False)):
            return None
        raw = cfg.get("instance_matrices")
        if raw is None:
            return None
        try:
            matrices = np.asarray(raw, dtype="f4")
        except Exception:
            return None
        if matrices.ndim == 2 and matrices.shape[1] == 16:
            matrices = matrices.reshape((-1, 4, 4))
        elif matrices.ndim == 3 and matrices.shape[1:] == (4, 4):
            pass
        else:
            return None
        if matrices.shape[0] <= 0:
            return None
        try:
            finite = np.isfinite(matrices).all(axis=(1, 2))
            if not bool(np.all(finite)):
                matrices = matrices[finite]
        except Exception:
            pass
        if matrices.shape[0] <= 0:
            return None
        return matrices.astype("f4", copy=False)

    def _mgl_copy_to_points_bounds(self, cfg: dict):
        if np is None or not isinstance(cfg, dict):
            return None
        try:
            bmin = np.asarray(cfg.get("bounds_min"), dtype="f4").reshape(-1)
            bmax = np.asarray(cfg.get("bounds_max"), dtype="f4").reshape(-1)
        except Exception:
            return None
        if bmin.size < 3 or bmax.size < 3:
            return None
        bmin = bmin[:3].astype("f4", copy=False)
        bmax = bmax[:3].astype("f4", copy=False)
        try:
            if not bool(np.all(np.isfinite(bmin))) or not bool(np.all(np.isfinite(bmax))):
                return None
        except Exception:
            return None
        return bmin, bmax

    def _mgl_mesh_entries_for_payload(self, payload: dict) -> List[Dict[str, object]]:
        entries: List[Dict[str, object]] = []
        if not isinstance(payload, dict):
            return entries
        entry = payload.get("mesh_entry")
        if isinstance(entry, dict):
            entries.append(entry)
        for sub in list(payload.get("submeshes") or []):
            if isinstance(sub, dict):
                entries.append(sub)
        return entries

    def _mgl_setup_copy_to_points_instances(self, item: MGLSceneItem) -> int:
        if self._mgl_ctx is None or self._mgl_prog is None or item is None:
            return 1
        payload = item.payload or {}
        cfg = payload.get("copy_to_points")
        matrices = self._mgl_copy_to_points_instance_matrices(cfg if isinstance(cfg, dict) else {})
        if matrices is None:
            return 1
        count = int(matrices.shape[0])
        if count <= 0:
            return 1
        entries = self._mgl_mesh_entries_for_payload(payload)
        if not entries:
            return 1
        try:
            instance_buffers = tuple(
                self._mgl_ctx.buffer(np.ascontiguousarray(matrices[:, idx, :], dtype="f4").tobytes())
                for idx in range(4)
            )
        except Exception:
            return 1
        resources = getattr(item, "resources", None)
        if not isinstance(resources, list):
            resources = []
        for buf in instance_buffers:
            resources.append(buf)
        ready_entries = 0
        for entry in entries:
            try:
                vbo = entry.get("vbo")
                nbo = entry.get("nbo")
                tbo = entry.get("tbo")
                cbo = entry.get("cbo")
                ibo = entry.get("ibo")
                if vbo is None or nbo is None or tbo is None or cbo is None:
                    continue
                vao = self._mgl_ctx.vertex_array(
                    self._mgl_prog,
                    self._mgl_mesh_vao_content(vbo, nbo, tbo, cbo, instance_buffers),
                    ibo,
                    4,
                )
                entry["instanced_vao"] = vao
                entry["_instance_buffers"] = instance_buffers
                entry["instance_count"] = count
                resources.append(vao)
                ready_entries += 1
            except Exception:
                continue
        if ready_entries <= 0:
            return 1
        payload["gpu_instancing"] = True
        payload["instance_count"] = count
        payload["_instance_buffers"] = instance_buffers
        payload["_copy_to_points_base_instance_matrices"] = matrices.astype("f4", copy=True)
        payload["_copy_to_points_current_instance_matrices"] = matrices.astype("f4", copy=True)
        payload["_music_effects_instance_applied"] = False
        item.payload = payload
        item.resources = resources
        return count

    def _mgl_qimage_from_texture(self, texture: object) -> Optional[QtGui.QImage]:
        if isinstance(texture, QtGui.QImage):
            return texture
        if PILImage is not None and isinstance(texture, PILImage.Image):
            image = texture
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")
            mode = image.mode
            data = image.tobytes()
            if mode == "RGB":
                fmt = QtGui.QImage.Format_RGB888
                stride = image.width * 3
            else:
                fmt = (
                    QtGui.QImage.Format_RGBA8888
                    if hasattr(QtGui.QImage, "Format_RGBA8888")
                    else QtGui.QImage.Format_ARGB32
                )
                stride = image.width * 4
            qimg = QtGui.QImage(data, image.width, image.height, stride, fmt)
            return qimg.copy()
        if np is not None and isinstance(texture, np.ndarray):
            arr = texture
            if arr.ndim == 2:
                arr = np.stack([arr] * 3, axis=-1)
            if arr.ndim != 3 or arr.shape[2] not in (3, 4):
                return None
            if arr.dtype != np.uint8:
                arr = np.clip(arr, 0.0, 1.0)
                arr = (arr * 255.0).astype(np.uint8)
            h, w = arr.shape[:2]
            if arr.shape[2] == 3:
                fmt = QtGui.QImage.Format_RGB888
                stride = w * 3
            else:
                fmt = (
                    QtGui.QImage.Format_RGBA8888
                    if hasattr(QtGui.QImage, "Format_RGBA8888")
                    else QtGui.QImage.Format_ARGB32
                )
                stride = w * 4
            qimg = QtGui.QImage(arr.tobytes(), w, h, stride, fmt)
            return qimg.copy()
        return None

    def _mgl_provider_image(self, provider: object) -> Optional[QtGui.QImage]:
        if provider is None:
            return None
        img = None
        try:
            img = getattr(provider, "image", None)
            if callable(img):
                img = img()
        except Exception:
            img = None
        if img is None:
            try:
                getter = getattr(provider, "get_image", None)
                if callable(getter):
                    img = getter()
            except Exception:
                img = None
        if img is None:
            return None
        if isinstance(img, QtGui.QImage):
            return img
        try:
            return self._mgl_qimage_from_texture(img)
        except Exception:
            return None

    def _mgl_proc_state(self, provider: object) -> Optional[dict]:
        if provider is None:
            return None
        try:
            fn = getattr(provider, "gpu_state", None)
            if callable(fn):
                state = fn()
                if isinstance(state, dict) and state:
                    return state
        except Exception:
            return None
        return None

    def _mgl_proc_seed_value(self, proc_state: Optional[dict]) -> float:
        base_seed = 0.0
        if isinstance(proc_state, dict):
            try:
                base_seed = float(proc_state.get("seed", 0.0) or 0.0)
            except Exception:
                base_seed = 0.0
        try:
            override_seed = int(getattr(self, "_timeline_texture_seed", 0) or 0)
        except Exception:
            override_seed = 0
        if override_seed != 0:
            return float(override_seed)
        return float(base_seed)

    def _mgl_ensure_proc_glyph(self, state: dict) -> Optional[object]:
        if self._mgl_ctx is None or not isinstance(state, dict):
            return None
        atlas = state.get("glyph_atlas")
        grid = state.get("glyph_grid") or (1, 1)
        if atlas is None and state.get("layer"):
            for key in ("overlay", "base"):
                sub = state.get(key)
                if isinstance(sub, dict):
                    atlas = sub.get("glyph_atlas")
                    if atlas is not None:
                        grid = sub.get("glyph_grid") or grid
                        break
        if atlas is None:
            return None
        key = id(atlas)
        if key != getattr(self, "_mgl_proc_glyph_key", None) or getattr(self, "_mgl_proc_glyph_tex", None) is None:
            try:
                if isinstance(atlas, QtGui.QImage):
                    qimg = atlas
                else:
                    qimg = self._mgl_qimage_from_texture(atlas)
                if qimg is None or qimg.isNull():
                    return None
                if hasattr(QtGui.QImage, "Format_RGBA8888"):
                    qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                else:
                    qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                qimg = qimg.mirrored(False, True)
                tex = self._mgl_make_texture(qimg)
                try:
                    tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
                    tex.repeat_x = False
                    tex.repeat_y = False
                except Exception:
                    pass
            except Exception:
                return None
            try:
                prev = getattr(self, "_mgl_proc_glyph_tex", None)
                if prev is not None:
                    prev.release()
            except Exception:
                pass
            try:
                self._mgl_proc_glyph_tex = tex
                self._mgl_proc_glyph_grid = (int(grid[0]), int(grid[1]))
                self._mgl_proc_glyph_key = key
            except Exception:
                pass
        return getattr(self, "_mgl_proc_glyph_tex", None)

    def _mgl_apply_procedural_uniforms_to(self, prog, state: Optional[dict]) -> None:
        if prog is None:
            return

        def _set_uniform(name: str, value) -> None:
            try:
                prog[name].value = value
            except Exception:
                pass

        def _apply_state(proc_state: Optional[dict], suffix: str = "") -> int:
            if not isinstance(proc_state, dict):
                proc_state = {}
            mode = str(proc_state.get("mode") or "").strip().lower()
            proc_mode = 1 if mode == "matrix_rain" else 0
            _set_uniform(f"ProceduralMode{suffix}", int(proc_mode))
            try:
                direction = proc_state.get("direction", None)
                if direction is None or direction == "":
                    invert_val = float(proc_state.get("invert", 0.0) or 0.0)
                    direction = 1.0 if invert_val >= 0.5 else 0.0
                elif isinstance(direction, str):
                    key = direction.strip().lower()
                    direction = {
                        "down": 0.0,
                        "up": 1.0,
                        "right": 2.0,
                        "left": 3.0,
                    }.get(key, 0.0)
                try:
                    direction = float(direction)
                except Exception:
                    direction = 0.0
                params = (
                    float(proc_state.get("tiling", 1) or 1),
                    float(proc_state.get("pack_x", 1) or 1),
                    float(proc_state.get("pack_y", 1) or 1),
                    float(direction),
                )
                _set_uniform(f"ProcParams{suffix}", params)
            except Exception:
                pass
            try:
                off_x = float(proc_state.get("offset_x", 0.0) or 0.0)
            except Exception:
                off_x = 0.0
            try:
                off_y = float(proc_state.get("offset_y", 0.0) or 0.0)
            except Exception:
                off_y = 0.0
            _set_uniform(f"ProcOffset{suffix}", (float(off_x), float(off_y)))
            _set_uniform(f"ProcSeed{suffix}", float(self._mgl_proc_seed_value(proc_state)))
            speed_val = proc_state.get("speed", 1.0)
            if speed_val is None:
                speed_val = 1.0
            _set_uniform(f"ProcAnimSpeed{suffix}", float(speed_val))
            emissive_val = proc_state.get("emissive", 0.0)
            if emissive_val is None:
                emissive_val = 0.0
            _set_uniform(f"ProcEmissive{suffix}", float(emissive_val))
            light_mix = proc_state.get("light_mix", 1.0)
            if light_mix is None:
                light_mix = 1.0
            _set_uniform(f"ProcLightMix{suffix}", float(light_mix))
            softness_val = proc_state.get("softness", 0.35)
            if softness_val is None:
                softness_val = 0.35
            _set_uniform(f"ProcSoftness{suffix}", float(softness_val))
            pan_val = proc_state.get("pan", 1.0)
            if pan_val is None:
                pan_val = 1.0
            _set_uniform(f"ProcPan{suffix}", float(pan_val))
            life_min = proc_state.get("life_min", 3.0)
            life_max = proc_state.get("life_max", 14.0)
            if life_min is None:
                life_min = 3.0
            if life_max is None:
                life_max = 14.0
            _set_uniform(f"ProcLifeMin{suffix}", float(life_min))
            _set_uniform(f"ProcLifeMax{suffix}", float(max(float(life_min), float(life_max))))
            gap_min = proc_state.get("chain_gap_min", 0.8)
            gap_max = proc_state.get("chain_gap_max", 2.2)
            if gap_min is None:
                gap_min = 0.8
            if gap_max is None:
                gap_max = 2.2
            try:
                gap_min = float(gap_min)
            except Exception:
                gap_min = 0.8
            try:
                gap_max = float(gap_max)
            except Exception:
                gap_max = 2.2
            _set_uniform(f"ProcChainMin{suffix}", float(gap_min))
            _set_uniform(f"ProcChainMax{suffix}", float(max(float(gap_min), float(gap_max))))
            bg_enabled = 1 if float(proc_state.get("bg_enabled", 0.0) or 0.0) > 0.5 else 0
            _set_uniform(f"ProcBgEnabled{suffix}", int(bg_enabled))
            bg = proc_state.get("bg_color")
            if isinstance(bg, (list, tuple)) and len(bg) >= 4:
                _set_uniform(f"ProcBg{suffix}", (float(bg[0]), float(bg[1]), float(bg[2]), float(bg[3])))
            else:
                _set_uniform(f"ProcBg{suffix}", (0.0, 0.0, 0.0, 1.0))
            return proc_mode

        if not state:
            _set_uniform("UseProcedural", 0)
            _set_uniform("UseProceduralLayer", 0)
            _set_uniform("ProcLightMix", 1.0)
            _apply_state({}, "2")
            return

        is_layer = bool(isinstance(state, dict) and state.get("layer"))
        _set_uniform("UseProcedural", 1)
        _set_uniform("UseProceduralLayer", 1 if is_layer else 0)

        base_state = state
        overlay_state = {}
        if is_layer:
            base_state = state.get("base") if isinstance(state.get("base"), dict) else {}
            overlay_state = state.get("overlay") if isinstance(state.get("overlay"), dict) else {}

        proc_mode = _apply_state(base_state, "")
        proc_mode2 = _apply_state(overlay_state, "2") if is_layer else _apply_state({}, "2")

        _set_uniform("ProcTime", float(getattr(self, "_mgl_proc_time", 0.0) or 0.0))

        glyph_state = None
        if is_layer:
            if proc_mode2 == 1:
                glyph_state = overlay_state
            elif proc_mode == 1:
                glyph_state = base_state
        else:
            if proc_mode == 1:
                glyph_state = base_state
        if glyph_state is not None:
            tex = self._mgl_ensure_proc_glyph(glyph_state)
            if tex is not None:
                try:
                    tex.use(location=1)
                except Exception:
                    pass
            _set_uniform("ProcGlyph", 1)
            grid = glyph_state.get("glyph_grid") or getattr(self, "_mgl_proc_glyph_grid", (1, 1))
            try:
                _set_uniform("ProcGlyphGrid", (float(grid[0]), float(grid[1])))
            except Exception:
                pass
            count = glyph_state.get("glyph_count")
            if count is None:
                try:
                    count = int(grid[0]) * int(grid[1])
                except Exception:
                    count = 1
            _set_uniform("ProcGlyphCount", float(max(1, int(count))))

    def _mgl_apply_procedural_uniforms(self, state: Optional[dict]) -> None:
        if self._mgl_prog is None:
            return
        self._mgl_apply_procedural_uniforms_to(self._mgl_prog, state)

    def _mgl_upload_texture(self, image: QtGui.QImage, source_path: str = "") -> None:
        if image.isNull():
            raise RuntimeError("Texture load failed")
        if hasattr(QtGui.QImage, "Format_RGBA8888"):
            image = image.convertToFormat(QtGui.QImage.Format_RGBA8888)
        else:
            image = image.convertToFormat(QtGui.QImage.Format_ARGB32)
        image = image.mirrored(False, True)
        if self._mgl_texture is not None:
            try:
                self._mgl_texture.release()
            except Exception:
                pass
        self._mgl_texture = self._mgl_make_texture(image)
        self._mgl_texture_path = source_path
        if self._mgl_prog is not None:
            try:
                self._mgl_prog["UseTexture"].value = 1
            except Exception:
                pass

    def _mgl_ensure_thumb_resources(self) -> bool:
        if self._mgl_ctx is None or np is None:
            return False
        if getattr(self, "_mgl_thumb_prog", None) is not None and getattr(self, "_mgl_thumb_vao", None) is not None:
            return True
        try:
            prog = self._mgl_ctx.program(vertex_shader=_THUMB_VERT, fragment_shader=SHADERS["mesh_fragment"])
            prog["Texture"].value = 0
            prog["ShadowMap"].value = 7
            prog["SceneColorTex"].value = 5
            prog["UseTexture"].value = 0
            prog["UseLighting"].value = 0
            prog["UseShadows"].value = 0
            prog["LightDir"].value = self._mgl_light_direction_tuple()
            prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
            prog["AmbientLight"].value = float(self._mgl_effective_ambient_light())
            prog["ShadowBias"].value = float(self._mgl_effective_shadow_bias())
            prog["ShadowDarkness"].value = float(self._mgl_effective_shadow_darkness())
            size_f = float(getattr(self, "_mgl_shadow_map_size", 2048) or 2048)
            prog["ShadowMapSize"].value = (size_f, size_f)
            prog["UseProcedural"].value = 1
            prog["UseProceduralLayer"].value = 0
            prog["UseVolumeMask"].value = 0
            prog["UseSceneRefraction"].value = 0
            prog["MaterialIor"].value = 1.0
            prog["MaterialTransparency"].value = 0.0
            prog["MaterialTint"].value = (1.0, 1.0, 1.0)
            prog["MaterialFresnelAmount"].value = 0.0
            prog["MaterialFresnelColor"].value = (1.0, 1.0, 1.0)
            prog["CameraWorldPos"].value = (0.0, 0.0, 4.0)
            prog["ScreenSize"].value = (1.0, 1.0)
            prog["ProcGlyph"].value = 1
            prog["ProcGlyphGrid"].value = (1.0, 1.0)
            prog["ProcGlyphCount"].value = 1.0
            prog["ProcChainMin"].value = 0.8
            prog["ProcChainMax"].value = 2.2
            prog["ProcChainMin2"].value = 0.8
            prog["ProcChainMax2"].value = 2.2
            if np is not None:
                ident = np.eye(4, dtype="f4")
                try:
                    prog["Model"].write(ident.tobytes())
                    prog["LightMvp"].write(ident.tobytes())
                    prog["VolumeInv"].write(ident.tobytes())
                except Exception:
                    pass
        except Exception:
            return False
        quad = np.array(
            [
                -1.0, -1.0, 0.0, 0.0,
                1.0, -1.0, 1.0, 0.0,
                -1.0, 1.0, 0.0, 1.0,
                1.0, 1.0, 1.0, 1.0,
            ],
            dtype="f4",
        )
        try:
            vbo = self._mgl_ctx.buffer(quad.tobytes())
            vao = self._mgl_ctx.vertex_array(prog, [(vbo, "2f 2f", "in_pos", "in_uv")])
        except Exception:
            return False
        self._mgl_thumb_prog = prog
        self._mgl_thumb_vbo = vbo
        self._mgl_thumb_vao = vao
        return True

    def _mgl_get_thumb_fbo(self, size: int):
        if self._mgl_ctx is None:
            return None
        size = int(max(16, min(int(size), 512)))
        cur_size = int(getattr(self, "_mgl_thumb_size", 0) or 0)
        fbo = getattr(self, "_mgl_thumb_fbo", None)
        tex = getattr(self, "_mgl_thumb_tex", None)
        if fbo is not None and tex is not None and cur_size == size:
            return fbo
        try:
            if fbo is not None:
                fbo.release()
        except Exception:
            pass
        try:
            if tex is not None:
                tex.release()
        except Exception:
            pass
        try:
            tex = self._mgl_ctx.texture((size, size), 4)
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = False
            tex.repeat_y = False
            fbo = self._mgl_ctx.framebuffer(color_attachments=[tex])
        except Exception:
            return None
        self._mgl_thumb_size = size
        self._mgl_thumb_tex = tex
        self._mgl_thumb_fbo = fbo
        return fbo

    def _mgl_render_proc_thumbnail(self, provider: object, size: int, proc_time: Optional[float] = None) -> Optional[QtGui.QImage]:
        if self._mgl_ctx is None:
            return None
        if not self._mgl_ensure_thumb_resources():
            return None
        state = self._mgl_proc_state(provider)
        if not state:
            return None
        fbo = self._mgl_get_thumb_fbo(int(size))
        if fbo is None:
            return None
        prog = getattr(self, "_mgl_thumb_prog", None)
        vao = getattr(self, "_mgl_thumb_vao", None)
        if prog is None or vao is None:
            return None
        if proc_time is None:
            try:
                proc_time = float(getattr(self, "_mgl_proc_time", 0.0) or 0.0)
            except Exception:
                proc_time = 0.0
            if not proc_time:
                proc_time = time.perf_counter()
        restore_proc_time = None
        restore_proc_time_set = False
        try:
            restore_proc_time_set = hasattr(self, "_mgl_proc_time")
            restore_proc_time = getattr(self, "_mgl_proc_time", None)
            try:
                self._mgl_proc_time = float(proc_time)
            except Exception:
                pass
        except Exception:
            restore_proc_time_set = False
            restore_proc_time = None
        old_viewport = None
        try:
            old_viewport = self._mgl_ctx.viewport
        except Exception:
            old_viewport = None
        size = int(getattr(self, "_mgl_thumb_size", size) or size)
        try:
            fbo.use()
            try:
                self._mgl_ctx.viewport = (0, 0, size, size)
            except Exception:
                pass
            try:
                self._mgl_ctx.enable(moderngl.BLEND)
                self._mgl_ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
            except Exception:
                pass
            try:
                self._mgl_ctx.clear(0.0, 0.0, 0.0, 0.0)
            except Exception:
                pass

            try:
                prog["Color"].value = (1.0, 1.0, 1.0, 1.0)
                prog["UseTexture"].value = 0
                prog["UseMaterial"].value = 0
                prog["UseLighting"].value = 0
                prog["UseShadows"].value = 0
                prog["UseVolumeMask"].value = 0
                prog["UseSceneRefraction"].value = 0
                try:
                    prog["Light"].value = (1.0, 1.0, 1.0)
                except Exception:
                    pass
                prog["LightDir"].value = self._mgl_light_direction_tuple()
                prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
                prog["AmbientLight"].value = float(self._mgl_effective_ambient_light())
                prog["MaterialTransparency"].value = 0.0
                prog["MaterialIor"].value = 1.0
                prog["MaterialTint"].value = (1.0, 1.0, 1.0)
                prog["MaterialFresnelAmount"].value = 0.0
                prog["MaterialFresnelColor"].value = (1.0, 1.0, 1.0)
                prog["ScreenSize"].value = (1.0, 1.0)
                prog["CameraWorldPos"].value = (0.0, 0.0, 4.0)
                if np is not None:
                    ident = np.eye(4, dtype="f4")
                    try:
                        prog["Model"].write(ident.tobytes())
                        prog["LightMvp"].write(ident.tobytes())
                        prog["VolumeInv"].write(ident.tobytes())
                    except Exception:
                        pass
            except Exception:
                pass

            self._mgl_apply_procedural_uniforms_to(prog, state)
            try:
                vao.render(moderngl.TRIANGLE_STRIP)
            except Exception:
                return None

            data = fbo.read(components=4, alignment=1)
            fmt = (
                QtGui.QImage.Format_RGBA8888
                if hasattr(QtGui.QImage, "Format_RGBA8888")
                else QtGui.QImage.Format_ARGB32
            )
            qimg = QtGui.QImage(data, size, size, size * 4, fmt)
            qimg = qimg.mirrored(False, True)
            return qimg.copy()
        finally:
            if old_viewport is not None:
                try:
                    self._mgl_ctx.viewport = old_viewport
                except Exception:
                    pass
            if restore_proc_time_set:
                try:
                    self._mgl_proc_time = restore_proc_time
                except Exception:
                    pass
            elif restore_proc_time is None:
                try:
                    if hasattr(self, "_mgl_proc_time"):
                        delattr(self, "_mgl_proc_time")
                except Exception:
                    pass
            try:
                if hasattr(self._mgl_ctx, "screen"):
                    self._mgl_ctx.screen.use()
            except Exception:
                pass
            try:
                self._mgl_bind_default_fbo()
            except Exception:
                pass

    def render_proc_thumbnail(self, provider: object, size: int, proc_time: Optional[float] = None) -> Optional[QtGui.QImage]:
        if not getattr(self, "_use_moderngl", False):
            return None
        renderer = getattr(self, "_mgl_renderer", None) or self
        if renderer is None or not hasattr(renderer, "_mgl_render_proc_thumbnail"):
            return None
        size = int(max(16, min(int(size), 512)))
        try:
            if hasattr(self, "makeCurrent"):
                try:
                    self.makeCurrent()
                except Exception:
                    return None
                try:
                    return renderer._mgl_render_proc_thumbnail(provider, size, proc_time)
                finally:
                    try:
                        self.doneCurrent()
                    except Exception:
                        pass
            return renderer._mgl_render_proc_thumbnail(provider, size, proc_time)
        except Exception:
            return None

    def _mgl_update_procedural_textures(
        self,
        step: float,
        frame_id: int,
        *,
        proc_time_override: Optional[float] = None,
    ) -> None:
        if self._mgl_ctx is None:
            return
        if proc_time_override is not None:
            try:
                self._mgl_proc_time = max(0.0, float(proc_time_override))
            except Exception:
                self._mgl_proc_time = 0.0
        else:
            try:
                self._mgl_proc_time = float(getattr(self, "_mgl_proc_time", 0.0) or 0.0) + float(step)
            except Exception:
                self._mgl_proc_time = float(step)
        # Prevent huge ProcTime values causing hash precision collapse in shaders.
        try:
            if self._mgl_proc_time > 10000.0:
                self._mgl_proc_time = math.fmod(self._mgl_proc_time, 10000.0)
        except Exception:
            pass
        if step <= 0.0:
            return

        def _advance(provider) -> bool:
            if provider is None:
                return False
            fn = getattr(provider, "advance", None)
            if callable(fn):
                try:
                    return bool(fn(step, frame_id))
                except TypeError:
                    try:
                        return bool(fn(step))
                    except Exception:
                        return False
                except Exception:
                    return False
            return False

        provider = getattr(self, "_mgl_proc_provider", None)
        if provider is not None:
            gpu_state = self._mgl_proc_state(provider)
            if gpu_state:
                try:
                    self._mgl_proc_gpu_enabled = True
                    self._mgl_proc_gpu_state = gpu_state
                except Exception:
                    pass
                self._mgl_ensure_proc_glyph(gpu_state)
            else:
                changed = _advance(provider)
                rev = None
                try:
                    rev = int(getattr(provider, "revision", 0))
                except Exception:
                    rev = None
                if changed or (rev is not None and rev != getattr(self, "_mgl_proc_rev", None)):
                    img = self._mgl_provider_image(provider)
                    if img is not None and not img.isNull():
                        try:
                            label = getattr(self, "_mgl_proc_label", "") or "procedural"
                            self._mgl_upload_texture(img, label)
                            self._mgl_texture_override = True
                            self._mgl_texture_paths = [label]
                            if rev is not None:
                                self._mgl_proc_rev = rev
                        except Exception:
                            pass

        proc_map = getattr(self, "_mgl_scene_proc_textures_by_owner", None)
        if not isinstance(proc_map, dict):
            return
        for entry in proc_map.values():
            provider = entry.get("provider")
            if provider is None:
                continue
            gpu_state = self._mgl_proc_state(provider)
            if gpu_state:
                entry["gpu_state"] = gpu_state
                entry["gpu"] = True
                self._mgl_ensure_proc_glyph(gpu_state)
                continue
            entry.pop("gpu", None)
            entry.pop("gpu_state", None)
            changed = _advance(provider)
            rev = None
            try:
                rev = int(getattr(provider, "revision", 0))
            except Exception:
                rev = None
            if not changed and (rev is None or rev == entry.get("rev")):
                continue
            img = self._mgl_provider_image(provider)
            if img is None or img.isNull():
                continue
            if hasattr(QtGui.QImage, "Format_RGBA8888"):
                img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
            else:
                img = img.convertToFormat(QtGui.QImage.Format_ARGB32)
            img = img.mirrored(False, True)
            try:
                ptr = img.bits()
                ptr.setsize(img.sizeInBytes())
                data = bytes(ptr)
            except Exception:
                data = img.bits().tobytes()
            tex = entry.get("texture")
            w = int(img.width())
            h = int(img.height())
            try:
                if tex is None or getattr(tex, "size", None) != (w, h):
                    if tex is not None:
                        try:
                            tex.release()
                        except Exception:
                            pass
                    tex = self._mgl_ctx.texture((w, h), 4, data)
                    tex.build_mipmaps()
                    tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                    tex.repeat_x = True
                    tex.repeat_y = True
                    entry["texture"] = tex
                    subs = entry.get("subs") or []
                    for sub in subs:
                        try:
                            sub["texture"] = tex
                        except Exception:
                            pass
                else:
                    tex.write(data)
                    try:
                        tex.build_mipmaps()
                    except Exception:
                        pass
                if rev is not None:
                    entry["rev"] = rev
            except Exception:
                pass

        volume_map = getattr(self, "_mgl_scene_volume_overrides_by_owner", None)
        if not isinstance(volume_map, dict):
            return
        for overrides in volume_map.values():
            if not isinstance(overrides, (list, tuple)):
                continue
            for entry in overrides:
                provider = entry.get("provider")
                if provider is None:
                    continue
                gpu_state = self._mgl_proc_state(provider)
                if gpu_state:
                    entry["gpu_state"] = gpu_state
                    entry["gpu"] = True
                    self._mgl_ensure_proc_glyph(gpu_state)
                    continue
                entry.pop("gpu", None)
                entry.pop("gpu_state", None)
                changed = _advance(provider)
                rev = None
                try:
                    rev = int(getattr(provider, "revision", 0))
                except Exception:
                    rev = None
                if not changed and (rev is None or rev == entry.get("rev")):
                    continue
                img = self._mgl_provider_image(provider)
                if img is None or img.isNull():
                    continue
                if hasattr(QtGui.QImage, "Format_RGBA8888"):
                    img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
                else:
                    img = img.convertToFormat(QtGui.QImage.Format_ARGB32)
                img = img.mirrored(False, True)
                try:
                    ptr = img.bits()
                    ptr.setsize(img.sizeInBytes())
                    data = bytes(ptr)
                except Exception:
                    data = img.bits().tobytes()
                tex = entry.get("texture")
                w = int(img.width())
                h = int(img.height())
                try:
                    if tex is None or getattr(tex, "size", None) != (w, h):
                        if tex is not None:
                            try:
                                tex.release()
                            except Exception:
                                pass
                        tex = self._mgl_ctx.texture((w, h), 4, data)
                        tex.build_mipmaps()
                        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                        tex.repeat_x = True
                        tex.repeat_y = True
                        entry["texture"] = tex
                    else:
                        tex.write(data)
                        try:
                            tex.build_mipmaps()
                        except Exception:
                            pass
                    if rev is not None:
                        entry["rev"] = rev
                except Exception:
                    pass

    def _mgl_make_texture(self, image: QtGui.QImage):
        if image.isNull():
            raise RuntimeError("Texture load failed")
        ptr = image.bits()
        try:
            ptr.setsize(image.sizeInBytes())
            data = bytes(ptr)
        except Exception:
            data = image.bits().tobytes()
        texture = self._mgl_ctx.texture(
            (image.width(), image.height()),
            4,
            data,
        )
        texture.build_mipmaps()
        texture.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        texture.repeat_x = True
        texture.repeat_y = True
        return texture

    def _mgl_upload_texture_path(self, path: Path) -> bool:
        image = QtGui.QImage(str(path))
        if image.isNull():
            return False
        self._mgl_upload_texture(image, str(path))
        return True

    def _apply_texture_path(self, path: str) -> None:
        if not self._use_moderngl:
            return
        if not _HAS_MGL:
            self._mgl_error = "ModernGL dependencies unavailable"
            return
        if self._mgl_ctx is None:
            self._mgl_error = "ModernGL context not ready"
            return
        image = QtGui.QImage(path)
        if image.isNull():
            self._mgl_error = "Texture load failed"
            return
        try:
            self.makeCurrent()
            self._mgl_upload_texture(image, path)
            self._mgl_texture_override = True
            self._mgl_texture_paths = [str(path)]
            self._mgl_error = ""
        except Exception as exc:
            self._mgl_error = f"Texture upload failed: {exc}"
        finally:
            try:
                self.doneCurrent()
            except Exception:
                pass

    def _mgl_target_framebuffer_id(self) -> int:
        try:
            target = int(getattr(self, "_mgl_render_target_fbo_id", 0) or 0)
            if target > 0:
                return target
        except Exception:
            pass
        try:
            return int(self.defaultFramebufferObject())
        except Exception:
            return 0

    def _mgl_bind_default_fbo(self) -> None:
        try:
            if self._gl is not None and hasattr(self, "defaultFramebufferObject"):
                fbo = int(self._mgl_target_framebuffer_id())
                if fbo > 0:
                    self._gl.glBindFramebuffer(0x8D40, fbo)  # GL_FRAMEBUFFER
        except Exception:
            pass

    def _mgl_render_size(self) -> Tuple[int, int]:
        override = getattr(self, "_mgl_render_size_override", None)
        if isinstance(override, (list, tuple)) and len(override) >= 2:
            try:
                w = max(2, int(override[0]))
                h = max(2, int(override[1]))
                return w, h
            except Exception:
                pass
        try:
            return max(2, int(self.width())), max(2, int(self.height()))
        except Exception:
            return 2, 2

    def _mgl_active_render_viewport(self) -> Tuple[int, int, int, int]:
        vp_w, vp_h = self._mgl_render_size()
        full = (0, 0, max(2, int(vp_w)), max(2, int(vp_h)))
        if isinstance(getattr(self, "_mgl_render_size_override", None), (list, tuple)):
            return full
        owner_fn = getattr(self, "_camera_film_gate_owner", None)
        rect_fn = getattr(self, "_camera_film_gate_rect", None)
        if not callable(owner_fn) or not callable(rect_fn):
            return full
        try:
            owner = str(owner_fn() or "").strip()
        except Exception:
            owner = ""
        if not owner:
            return full
        try:
            gate, _visible, _aspect_w, _aspect_h = rect_fn(owner)
        except Exception:
            return full
        try:
            if gate is None or gate.isNull() or gate.width() <= 2.0 or gate.height() <= 2.0:
                return full
            x = int(round(float(gate.left())))
            w = int(round(float(gate.width())))
            h = int(round(float(gate.height())))
            y = int(round(float(vp_h) - float(gate.bottom())))
            x = max(0, min(max(0, int(vp_w) - 2), int(x)))
            y = max(0, min(max(0, int(vp_h) - 2), int(y)))
            w = max(2, min(int(vp_w) - int(x), int(w)))
            h = max(2, min(int(vp_h) - int(y), int(h)))
            return (int(x), int(y), int(w), int(h))
        except Exception:
            return full

    def _mgl_screen_to_active_ndc(self, px: int, py: int, viewport_w: int, viewport_h: int):
        active = getattr(self, "_mgl_active_viewport_rect", None)
        if not (isinstance(active, (list, tuple)) and len(active) >= 4):
            active = self._mgl_active_render_viewport()
        try:
            render_w, render_h = self._mgl_render_size()
            scale_x = float(viewport_w) / max(1.0, float(render_w))
            scale_y = float(viewport_h) / max(1.0, float(render_h))
            ax = float(active[0]) * scale_x
            ay = float(active[1]) * scale_y
            aw = max(1.0, float(active[2]) * scale_x)
            ah = max(1.0, float(active[3]) * scale_y)
            nx = (float(px) - ax) / aw
            ny = ((float(viewport_h) - float(py)) - ay) / ah
            if nx < 0.0 or nx > 1.0 or ny < 0.0 or ny > 1.0:
                return None
            return ((2.0 * nx) - 1.0, (2.0 * ny) - 1.0)
        except Exception:
            return None

    def _mgl_light_direction_tuple(self) -> Tuple[float, float, float]:
        scene_rot_dir = self._mgl_scene_light_direction_from_rotation()
        if scene_rot_dir is not None and np is not None:
            try:
                self._mgl_scene_light_dir = scene_rot_dir
                arr = np.asarray(scene_rot_dir, dtype=np.float32).reshape(-1)[:3]
                length = float(np.linalg.norm(arr))
                if length > 1.0e-6:
                    arr = arr / length
                    return (float(arr[0]), float(arr[1]), float(arr[2]))
            except Exception:
                pass
        try:
            if str(getattr(self, "_mgl_scene_light_owner", "") or "").strip():
                self._mgl_scene_light_dir = None
        except Exception:
            pass
        scene_dir = getattr(self, "_mgl_scene_light_dir", None)
        if scene_dir is not None and np is not None:
            try:
                arr = np.asarray(scene_dir, dtype=np.float32).reshape(-1)[:3]
                if arr.shape[0] >= 3 and np.all(np.isfinite(arr)):
                    length = float(np.linalg.norm(arr))
                    if length > 1.0e-6:
                        arr = arr / length
                        return (float(arr[0]), float(arr[1]), float(arr[2]))
            except Exception:
                pass
        if np is None:
            return (0.35, 0.85, 0.45)
        try:
            raw = getattr(self, "_mgl_shadow_light_dir", (0.35, 0.85, 0.45))
            arr = np.asarray(raw, dtype=np.float32).reshape(-1)
            if int(arr.shape[0]) < 3:
                arr = np.array([0.35, 0.85, 0.45], dtype=np.float32)
            else:
                arr = arr[:3].astype(np.float32, copy=True)
            if not np.all(np.isfinite(arr)):
                arr = np.array([0.35, 0.85, 0.45], dtype=np.float32)
            length = float(np.linalg.norm(arr))
            if length <= 1.0e-6:
                arr = np.array([0.35, 0.85, 0.45], dtype=np.float32)
                length = float(np.linalg.norm(arr))
            arr = arr / max(length, 1.0e-6)
            return (float(arr[0]), float(arr[1]), float(arr[2]))
        except Exception:
            return (0.35, 0.85, 0.45)

    @staticmethod
    def _mgl_normalize_light_type(value) -> str:
        text = str(value or "").strip().lower().replace("-", "_")
        text = " ".join(text.replace("_", " ").split())
        aliases = {
            "dir": "directional",
            "directional": "directional",
            "directional light": "directional",
            "directional_light": "directional",
            "point": "point",
            "point light": "point",
            "point_light": "point",
            "spot": "spot",
            "spot light": "spot",
            "spot_light": "spot",
            "spotlight": "spot",
            "area": "area",
            "area light": "area",
            "area_light": "area",
        }
        return aliases.get(text, aliases.get(text.replace(" ", "_"), "directional"))

    @staticmethod
    def _mgl_light_type_index(value) -> int:
        light_type = MGLRendererMixin._mgl_normalize_light_type(value)
        return {
            "directional": 0,
            "point": 1,
            "spot": 2,
            "area": 3,
        }.get(light_type, 0)

    @staticmethod
    def _mgl_light_guide_line_points(light_type: str):
        if np is None:
            return None
        kind = MGLRendererMixin._mgl_normalize_light_type(light_type)
        points = []

        def add(a, b):
            points.append((float(a[0]), float(a[1]), float(a[2])))
            points.append((float(b[0]), float(b[1]), float(b[2])))

        def add_ring(axis: str, radius: float = 0.32, segments: int = 32):
            coords = []
            for i in range(int(segments)):
                t = (math.tau * float(i)) / float(segments)
                c = math.cos(t) * radius
                s = math.sin(t) * radius
                if axis == "xy":
                    coords.append((c, s, 0.0))
                elif axis == "xz":
                    coords.append((c, 0.0, s))
                else:
                    coords.append((0.0, c, s))
            for i, a in enumerate(coords):
                add(a, coords[(i + 1) % len(coords)])

        if kind == "point":
            add_ring("xy")
            add_ring("xz")
            ray_dirs = [
                (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0), (0.0, -1.0, 0.0),
                (0.0, 0.0, 1.0), (0.0, 0.0, -1.0),
                (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0),
                (1.0, -1.0, 0.0), (-1.0, -1.0, 0.0),
            ]
            for d in ray_dirs:
                arr = np.asarray(d, dtype=np.float32)
                length = float(np.linalg.norm(arr))
                if length > 1.0e-6:
                    arr = arr / length
                add(tuple(arr * 0.38), tuple(arr * 0.62))
        elif kind == "spot":
            apex = (0.0, 0.0, 0.0)
            base_z = -0.95
            radius = 0.38
            segments = 32
            base = []
            for i in range(segments):
                t = (math.tau * float(i)) / float(segments)
                base.append((math.cos(t) * radius, math.sin(t) * radius, base_z))
            for i, a in enumerate(base):
                add(a, base[(i + 1) % len(base)])
            for i in range(0, segments, 4):
                add(apex, base[i])
            add(apex, (0.0, 0.0, base_z))
        elif kind == "area":
            s = 0.42
            corners = [(-s, s, 0.0), (s, s, 0.0), (s, -s, 0.0), (-s, -s, 0.0)]
            for i, a in enumerate(corners):
                add(a, corners[(i + 1) % len(corners)])
            add(corners[0], corners[2])
            add(corners[1], corners[3])
        else:
            add((-0.35, 0.35, 0.0), (0.35, 0.35, 0.0))
            add((0.35, 0.35, 0.0), (0.35, -0.35, 0.0))
            add((0.35, -0.35, 0.0), (-0.35, -0.35, 0.0))
            add((-0.35, -0.35, 0.0), (-0.35, 0.35, 0.0))
            add((-0.35, 0.35, 0.0), (0.35, -0.35, 0.0))
            add((0.35, 0.35, 0.0), (-0.35, -0.35, 0.0))
            add((0.0, 0.0, 0.0), (0.0, 0.0, -1.05))
            add((0.0, 0.0, -1.05), (-0.13, 0.0, -0.82))
            add((0.0, 0.0, -1.05), (0.13, 0.0, -0.82))
            add((0.0, 0.0, -1.05), (0.0, -0.13, -0.82))
            add((0.0, 0.0, -1.05), (0.0, 0.13, -0.82))
        return np.asarray(points, dtype="f4").reshape(-1, 3)

    def _mgl_light_position_tuple(self) -> Tuple[float, float, float]:
        try:
            owner = str(getattr(self, "_mgl_scene_light_owner", "") or "").strip()
            if owner:
                xf = self._mgl_get_scene_asset_xform(owner)
                if isinstance(xf, dict):
                    pos = list(xf.get("pos", (4.0, 6.0, 4.0)))[:3]
                    if len(pos) >= 3:
                        return (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            pass
        try:
            pos = list(getattr(self, "_mgl_scene_light_pos", (4.0, 6.0, 4.0)) or (4.0, 6.0, 4.0))[:3]
            if len(pos) >= 3:
                return (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            pass
        return (4.0, 6.0, 4.0)

    def _mgl_explicit_light_range_value(self) -> Optional[float]:
        try:
            scene_range = getattr(self, "_mgl_scene_light_range", None)
            if scene_range is not None:
                value = float(scene_range)
                if value > 0.0:
                    return max(0.001, min(100000.0, value))
        except Exception:
            pass
        return None

    def _mgl_explicit_shadow_range_value(self) -> Optional[float]:
        try:
            shadow_range = getattr(self, "_mgl_scene_light_shadow_range", None)
            if shadow_range is not None:
                value = float(shadow_range)
                if value > 0.0:
                    return max(0.001, min(100000.0, value))
        except Exception:
            pass
        return self._mgl_explicit_light_range_value()

    def _mgl_light_range_value(self) -> float:
        explicit = self._mgl_explicit_light_range_value()
        if explicit is not None:
            return float(explicit)
        shadow_explicit = self._mgl_explicit_shadow_range_value()
        if shadow_explicit is not None:
            return float(shadow_explicit)
        try:
            bounds = self._mgl_shadow_scene_bounds()
            if bounds is not None:
                _center, radius = bounds
                return max(300.0, min(100000.0, float(radius) * 6.0))
        except Exception:
            pass
        return 300.0

    def _mgl_uses_point_shadow_atlas(self) -> bool:
        try:
            return self._mgl_normalize_light_type(
                getattr(self, "_mgl_scene_light_type", "directional")
            ) == "point"
        except Exception:
            return False

    @staticmethod
    def _mgl_auto_shadow_near(far: float) -> float:
        try:
            far_value = max(2.0, float(far))
        except Exception:
            far_value = 300.0
        return max(0.005, min(0.05, far_value * 0.0002))

    def _mgl_point_shadow_near(self, far: float) -> float:
        try:
            override = getattr(self, "_mgl_scene_light_shadow_near", None)
            if override is not None and float(override) > 0.0:
                return max(0.001, min(max(0.001, float(far) * 0.5), float(override)))
        except Exception:
            pass
        try:
            far_value = max(2.0, float(far))
        except Exception:
            far_value = 300.0
        return max(0.02, min(1.0, far_value * 0.0005))

    def _mgl_scene_light_direction_from_rotation(self):
        if np is None:
            return None
        owner = str(getattr(self, "_mgl_scene_light_owner", "") or "").strip()
        if not owner:
            return None
        try:
            xf = self._mgl_get_scene_asset_xform(owner)
            rot = list(xf.get("rot", (0.0, 0.0, 0.0)))[:3] if isinstance(xf, dict) else (0.0, 0.0, 0.0)
            rx, ry, rz = (float(rot[0]), float(rot[1]), float(rot[2]))
        except Exception:
            rx = ry = rz = 0.0

        def _rx(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[1, 1] = c
            m[1, 2] = s
            m[2, 1] = -s
            m[2, 2] = c
            return m

        def _ry(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = c
            m[0, 2] = -s
            m[2, 0] = s
            m[2, 2] = c
            return m

        def _rz(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = c
            m[0, 1] = s
            m[1, 0] = -s
            m[1, 1] = c
            return m

        try:
            rmat = _rx(-rx) @ _ry(-ry) @ _rz(-rz)
            ray_dir = (np.array([0.0, 0.0, -1.0, 0.0], dtype=np.float32) @ rmat)[:3]
            length = float(np.linalg.norm(ray_dir))
            if length <= 1.0e-6:
                return None
            ray_dir = ray_dir / length
            light_dir = -ray_dir
            return (float(light_dir[0]), float(light_dir[1]), float(light_dir[2]))
        except Exception:
            return None

    def _mgl_effective_light_intensity(self) -> float:
        try:
            scene_value = getattr(self, "_mgl_scene_light_intensity", None)
            if scene_value is not None:
                return max(0.0, float(scene_value))
        except Exception:
            pass
        try:
            return max(0.0, float(getattr(self, "_mgl_light_intensity", 1.0) or 1.0))
        except Exception:
            return 1.0

    def _mgl_effective_ambient_light(self) -> float:
        try:
            if not bool(getattr(self, "_mgl_ambient_light_enabled", True)):
                return 0.0
        except Exception:
            pass
        try:
            return max(0.0, min(1.0, float(getattr(self, "_mgl_ambient_light_strength", 0.10))))
        except Exception:
            return 0.10

    def _mgl_effective_shadow_darkness(self) -> float:
        try:
            raw_base = getattr(self, "_mgl_shadow_darkness", 1.0)
            base = 1.0 if raw_base is None else float(raw_base)
        except Exception:
            base = 1.0
        try:
            strength = getattr(self, "_mgl_scene_light_shadow_strength", None)
            if strength is not None:
                base *= max(0.0, min(1.0, float(strength)))
        except Exception:
            pass
        return max(0.0, min(1.0, float(base)))

    def _mgl_effective_shadow_bias(self) -> float:
        try:
            override = getattr(self, "_mgl_scene_light_shadow_bias", None)
            if override is not None and float(override) > 0.0:
                return max(0.0, min(0.1, float(override)))
        except Exception:
            pass
        try:
            base = max(0.0, min(0.1, float(getattr(self, "_mgl_shadow_bias", 0.0009) or 0.0009)))
        except Exception:
            base = 0.0009
        try:
            light_type = self._mgl_normalize_light_type(getattr(self, "_mgl_scene_light_type", "directional"))
            if light_type in {"point", "spot", "area"}:
                return min(base, 0.00005)
        except Exception:
            pass
        return base

    def _mgl_spot_shadow_fov_value(self) -> float:
        try:
            override = getattr(self, "_mgl_scene_light_shadow_fov", None)
            if override is not None and float(override) > 0.0:
                return max(1.0, min(179.0, float(override)))
        except Exception:
            pass
        return 64.0

    def _mgl_spot_cone_cosines(self) -> Tuple[float, float]:
        full_fov = self._mgl_spot_shadow_fov_value()
        outer_angle = max(0.5, min(89.5, float(full_fov) * 0.5))
        inner_angle = max(0.05, min(outer_angle - 0.05, outer_angle * 0.5))
        return (
            float(math.cos(math.radians(inner_angle))),
            float(math.cos(math.radians(outer_angle))),
        )

    def _mgl_shadow_map_size_value(self) -> int:
        quality = self._mgl_shadow_quality_value(
            getattr(
                self,
                "_mgl_shadow_render_quality",
                "high",
            )
            if bool(getattr(self, "_mgl_shadow_render_active", False))
            else getattr(self, "_mgl_shadow_quality", "low")
        )
        size_by_quality = {
            "low": 512,
            "medium": 1024,
            "high": 2048,
            "ultra": 4096,
        }
        if quality in size_by_quality:
            size = int(size_by_quality[quality])
        else:
            try:
                size = int(getattr(self, "_mgl_shadow_map_size", 2048) or 2048)
            except Exception:
                size = 2048
        try:
            light_type = self._mgl_normalize_light_type(getattr(self, "_mgl_scene_light_type", "directional"))
            if light_type == "area":
                size = max(int(size), 2048)
        except Exception:
            pass
        return max(256, min(4096, int(size)))

    def _mgl_shadow_tile_size_value(self) -> int:
        size = self._mgl_shadow_map_size_value()
        if self._mgl_uses_point_shadow_atlas():
            tile = max(1024, min(2048, int(size)))
            try:
                info = getattr(self._mgl_ctx, "info", {}) if getattr(self, "_mgl_ctx", None) is not None else {}
                max_texture = int(info.get("GL_MAX_TEXTURE_SIZE", 0) or 0) if isinstance(info, dict) else 0
                if max_texture > 0:
                    tile = min(tile, max(256, int(max_texture) // 3))
            except Exception:
                pass
            return max(256, int(tile))
        return int(size)

    def _mgl_shadow_texture_dimensions(self) -> Tuple[int, int]:
        tile_size = self._mgl_shadow_tile_size_value()
        if self._mgl_uses_point_shadow_atlas():
            return int(tile_size * 3), int(tile_size * 2)
        return int(tile_size), int(tile_size)

    @staticmethod
    def _mgl_shadow_quality_value(value) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "l": "low",
            "lo": "low",
            "low": "low",
            "m": "medium",
            "med": "medium",
            "medium": "medium",
            "h": "high",
            "hi": "high",
            "high": "high",
            "u": "ultra",
            "ultra": "ultra",
            "max": "ultra",
        }
        return aliases.get(text, "low")

    def _mgl_shadow_update_interval_value(self) -> float:
        return 0.0

    def _mgl_ensure_shadow_resources(self) -> bool:
        if self._mgl_ctx is None or getattr(self, "_mgl_shadow_prog", None) is None:
            return False
        size = self._mgl_shadow_texture_dimensions()
        need_id_map = not bool(getattr(self, "_mgl_self_shadows_enabled", True))
        if (
            getattr(self, "_mgl_shadow_fbo", None) is not None
            and getattr(self, "_mgl_shadow_depth_tex", None) is not None
            and (bool(getattr(self, "_mgl_shadow_id_tex", None) is not None) == bool(need_id_map))
            and tuple(getattr(self, "_mgl_shadow_size_current", ()) or ()) == tuple(size)
        ):
            return True
        try:
            fbo = getattr(self, "_mgl_shadow_fbo", None)
            if fbo is not None and hasattr(fbo, "release"):
                fbo.release()
        except Exception:
            pass
        try:
            tex = getattr(self, "_mgl_shadow_depth_tex", None)
            if tex is not None and hasattr(tex, "release"):
                tex.release()
        except Exception:
            pass
        try:
            tex = getattr(self, "_mgl_shadow_id_tex", None)
            if tex is not None and hasattr(tex, "release"):
                tex.release()
        except Exception:
            pass
        try:
            depth = self._mgl_ctx.depth_texture(tuple(size))
            try:
                depth.repeat_x = False
                depth.repeat_y = False
            except Exception:
                pass
            try:
                if self._mgl_uses_point_shadow_atlas():
                    # The point atlas needs exact face-local depth reads; linear
                    # filtering can blend between adjacent cube-face tiles.
                    depth.filter = (moderngl.NEAREST, moderngl.NEAREST)
                else:
                    depth.filter = (moderngl.LINEAR, moderngl.LINEAR)
            except Exception:
                pass
            try:
                depth.compare_func = ""
            except Exception:
                pass
            id_tex = None
            if need_id_map:
                id_tex = self._mgl_ctx.texture(tuple(size), 1, dtype="f4")
                try:
                    id_tex.repeat_x = False
                    id_tex.repeat_y = False
                except Exception:
                    pass
                try:
                    id_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
                except Exception:
                    pass
                fbo = self._mgl_ctx.framebuffer(color_attachments=[id_tex], depth_attachment=depth)
            else:
                fbo = self._mgl_ctx.framebuffer(depth_attachment=depth)
        except Exception as exc:
            try:
                self._mgl_log_throttled(
                    "_mgl_shadow_resource_error_ts",
                    "shadows: resource creation failed err=" + repr(exc),
                    2.0,
                )
            except Exception:
                pass
            self._mgl_shadow_fbo = None
            self._mgl_shadow_depth_tex = None
            self._mgl_shadow_id_tex = None
            self._mgl_shadow_size_current = ()
            return False
        self._mgl_shadow_fbo = fbo
        self._mgl_shadow_depth_tex = depth
        self._mgl_shadow_id_tex = id_tex
        self._mgl_shadow_size_current = tuple(size)
        return True

    def _mgl_shadow_scene_bounds(self):
        if np is None:
            return None
        mins_list = []
        maxs_list = []
        visibility = getattr(self, "_mgl_scene_visibility", {}) or {}

        def _add_bounds(owner: str, bounds) -> None:
            if bounds is None:
                return
            try:
                bmin, bmax = bounds
                bmin = np.asarray(bmin, dtype=np.float32).reshape(-1)[:3]
                bmax = np.asarray(bmax, dtype=np.float32).reshape(-1)[:3]
                if bmin.shape[0] < 3 or bmax.shape[0] < 3:
                    return
                if not np.all(np.isfinite(bmin)) or not np.all(np.isfinite(bmax)):
                    return
                mins_list.append(bmin.astype(np.float32, copy=True))
                maxs_list.append(bmax.astype(np.float32, copy=True))
            except Exception:
                return

        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            try:
                for item in scene.items():
                    if not bool(getattr(item, "visible", False)):
                        continue
                    payload = getattr(item, "payload", None) or {}
                    owner = str(payload.get("owner") or payload.get("node") or "").strip()
                    if owner and not bool(visibility.get(owner, True)):
                        continue
                    if owner:
                        try:
                            tag = str(getattr(item, "tag", "") or "")
                            if tag == "scene-light":
                                continue
                        except Exception:
                            pass
                        try:
                            _add_bounds(owner, self.get_scene_owner_bounds(owner))
                        except Exception:
                            pass
            except Exception:
                pass

        try:
            splat_bounds = getattr(self, "_mgl_scene_splat_bounds_by_owner", None)
            if isinstance(splat_bounds, dict):
                for owner, bounds in splat_bounds.items():
                    owner_key = str(owner or "").strip()
                    if owner_key and not bool(visibility.get(owner_key, True)):
                        continue
                    _add_bounds(owner_key, bounds)
        except Exception:
            pass

        if not mins_list or not maxs_list:
            try:
                center = np.asarray(getattr(self, "_mgl_center", None), dtype=np.float32).reshape(-1)[:3]
                if center.shape[0] < 3 or not np.all(np.isfinite(center)):
                    raise ValueError("invalid center")
            except Exception:
                center = np.zeros(3, dtype=np.float32)
            try:
                radius = max(1.0, float(getattr(self, "_mgl_camera_zoom", 1.0) or 1.0))
            except Exception:
                radius = 1.0
            return center.astype(np.float32), float(radius)

        bmin = np.min(np.stack(mins_list, axis=0), axis=0)
        bmax = np.max(np.stack(maxs_list, axis=0), axis=0)
        center = ((bmin + bmax) * 0.5).astype(np.float32)
        diag = bmax - bmin
        radius = max(0.5, float(np.linalg.norm(diag) * 0.5), float(np.max(np.abs(diag))) * 0.5)
        return center, float(radius)

    def _mgl_shadow_light_matrices(self):
        if np is None or Matrix44 is None:
            return None
        bounds = self._mgl_shadow_scene_bounds()
        if bounds is None:
            return None
        center, radius = bounds
        light_type = self._mgl_normalize_light_type(getattr(self, "_mgl_scene_light_type", "directional"))
        try:
            self._mgl_shadow_point_faces = None
            self._mgl_shadow_light_mvp_faces = None
        except Exception:
            pass
        scene_light_dir = self._mgl_scene_light_direction_from_rotation()
        if scene_light_dir is not None:
            try:
                self._mgl_scene_light_dir = scene_light_dir
            except Exception:
                pass
        else:
            try:
                self._mgl_scene_light_dir = None
            except Exception:
                pass
        light_dir = np.asarray(self._mgl_light_direction_tuple(), dtype=np.float32)
        if light_type in {"point", "spot", "area"}:
            try:
                light_pos = np.asarray(self._mgl_light_position_tuple(), dtype=np.float32).reshape(-1)[:3]
                if light_pos.shape[0] < 3 or not np.all(np.isfinite(light_pos)):
                    raise ValueError("invalid light position")
            except Exception:
                light_pos = center + light_dir * max(4.0, float(radius) * 4.0)
            ray_dir = -light_dir
            ray_len = float(np.linalg.norm(ray_dir))
            if ray_len <= 1.0e-6 or not np.all(np.isfinite(ray_dir)):
                ray_dir = np.array([0.0, 0.0, -1.0], dtype=np.float32)
            else:
                ray_dir = ray_dir / ray_len
            to_center = center - light_pos
            to_center_len = float(np.linalg.norm(to_center))
            if light_type == "point" and self._mgl_uses_point_shadow_atlas():
                try:
                    auto_far = max(300.0, float(to_center_len) + float(radius) * 4.0)
                    far = auto_far
                    explicit_range = self._mgl_explicit_shadow_range_value()
                    if explicit_range is not None and float(explicit_range) > 0.0:
                        far = max(2.0, min(100000.0, float(explicit_range)))
                    near = self._mgl_point_shadow_near(float(far))
                    proj = Matrix44.perspective_projection(92.0, 1.0, float(near), float(far))
                    tile_size = int(self._mgl_shadow_tile_size_value())
                    eye = (
                        float(light_pos[0]),
                        float(light_pos[1]),
                        float(light_pos[2]),
                    )
                    face_defs = [
                        ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), 0, 0),
                        ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), 1, 0),
                        ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), 2, 0),
                        ((0.0, -1.0, 0.0), (0.0, 0.0, -1.0), 0, 1),
                        ((0.0, 0.0, 1.0), (0.0, -1.0, 0.0), 1, 1),
                        ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0), 2, 1),
                    ]
                    faces = []
                    for direction, up_vec, col, row in face_defs:
                        target = (
                            float(light_pos[0]) + float(direction[0]),
                            float(light_pos[1]) + float(direction[1]),
                            float(light_pos[2]) + float(direction[2]),
                        )
                        view = Matrix44.look_at(eye, target, up_vec, dtype="f4")
                        mvp = (proj * view).astype("f4")
                        faces.append(
                            {
                                "proj": proj,
                                "view": view,
                                "mvp": mvp,
                                "viewport": (
                                    int(col) * tile_size,
                                    int(row) * tile_size,
                                    tile_size,
                                    tile_size,
                                ),
                            }
                        )
                    self._mgl_shadow_point_faces = faces
                    self._mgl_shadow_light_mvp_faces = [face["mvp"] for face in faces]
                    return proj, faces[0]["view"], faces[0]["mvp"], center, max(float(radius), float(far))
                except Exception:
                    return None
            if light_type == "area":
                forward = ray_dir
                forward_len = float(np.linalg.norm(forward))
                if forward_len <= 1.0e-6 or not np.all(np.isfinite(forward)):
                    forward = np.array([0.0, 0.0, -1.0], dtype=np.float32)
                else:
                    forward = forward / forward_len
                extent = max(1.0, float(radius) * 2.5)
                auto_far = max(2.0, float(to_center_len) + float(radius) * 4.0)
                far = auto_far
                try:
                    explicit_range = self._mgl_explicit_shadow_range_value()
                    if explicit_range is not None and float(explicit_range) > 0.0:
                        value = min(100000.0, float(explicit_range))
                        extent = max(extent, value * 0.5)
                        far = max(2.0, value)
                except Exception:
                    pass
                eye = light_pos
                target = eye + forward
                up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
                if abs(float(np.dot(up, forward))) > 0.92:
                    up = np.array([1.0, 0.0, 0.0], dtype=np.float32)
                try:
                    near = self._mgl_auto_shadow_near(far)
                    view = Matrix44.look_at(
                        (float(eye[0]), float(eye[1]), float(eye[2])),
                        (float(target[0]), float(target[1]), float(target[2])),
                        (float(up[0]), float(up[1]), float(up[2])),
                        dtype="f4",
                    )
                    proj = Matrix44.orthogonal_projection(
                        -extent,
                        extent,
                        extent,
                        -extent,
                        float(near),
                        float(far),
                        dtype="f4",
                    )
                    return proj, view, (proj * view).astype("f4"), center, float(extent)
                except Exception:
                    return None
            if light_type == "spot":
                forward = ray_dir
                fov = self._mgl_spot_shadow_fov_value()
            elif to_center_len > max(0.05, float(radius) * 0.02):
                forward = to_center / to_center_len
                fov = 179.0
            else:
                forward = ray_dir
                fov = 179.0
            try:
                override_fov = getattr(self, "_mgl_scene_light_shadow_fov", None)
                if override_fov is not None and float(override_fov) > 0.0:
                    fov = max(1.0, min(179.0, float(override_fov)))
            except Exception:
                pass
            forward_len = float(np.linalg.norm(forward))
            if forward_len <= 1.0e-6 or not np.all(np.isfinite(forward)):
                forward = np.array([0.0, 0.0, -1.0], dtype=np.float32)
            else:
                forward = forward / forward_len
            up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
            if abs(float(np.dot(up, forward))) > 0.92:
                up = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            try:
                auto_far = max(2.0, float(to_center_len) + float(radius) * 4.0)
                far_from_light = auto_far
                explicit_range = self._mgl_explicit_shadow_range_value()
                if explicit_range is not None and float(explicit_range) > 0.0:
                    far_from_light = max(2.0, min(100000.0, float(explicit_range)))
                # Point-light shadows must originate at the outliner light position.
                # Backing the shadow camera away avoids near-plane clipping, but it
                # makes cast shadows trace back to a different location than LightPos.
                shadow_backoff = 0.0
                eye = light_pos - (forward * float(shadow_backoff))
                target = light_pos + forward
                far = max(2.0, float(far_from_light) + float(shadow_backoff))
                near = self._mgl_auto_shadow_near(far)
                view = Matrix44.look_at(
                    (float(eye[0]), float(eye[1]), float(eye[2])),
                    (float(target[0]), float(target[1]), float(target[2])),
                    (float(up[0]), float(up[1]), float(up[2])),
                    dtype="f4",
                )
                proj = Matrix44.perspective_projection(float(fov), 1.0, float(near), float(far))
                return proj, view, (proj * view).astype("f4"), center, float(radius)
            except Exception:
                return None
        extent = max(1.0, float(radius) * 2.5)
        distance = extent * 4.0
        eye = center + light_dir * distance
        up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        if abs(float(np.dot(up, light_dir))) > 0.92:
            up = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        try:
            view = Matrix44.look_at(
                (float(eye[0]), float(eye[1]), float(eye[2])),
                (float(center[0]), float(center[1]), float(center[2])),
                (float(up[0]), float(up[1]), float(up[2])),
                dtype="f4",
            )
            proj = Matrix44.orthogonal_projection(
                -extent,
                extent,
                extent,
                -extent,
                0.01,
                max(2.0, extent * 12.0),
                dtype="f4",
            )
            return proj, view, (proj * view).astype("f4"), center, float(radius)
        except Exception:
            return None

    def _mgl_shadow_scene_signature(self, size, center, radius) -> tuple:
        if np is None:
            try:
                return (tuple(int(v) for v in size),)
            except Exception:
                return (int(size),)
        try:
            frame = int(self._mgl_timeline_frame_index())
        except Exception:
            frame = 0
        try:
            size_sig = tuple(int(v) for v in size)
        except Exception:
            size_sig = int(size)
        parts = [
            ("size", size_sig),
            ("quality", self._mgl_shadow_quality_value(getattr(self, "_mgl_shadow_quality", "low"))),
            ("render", 1 if bool(getattr(self, "_mgl_shadow_render_active", False)) else 0),
            ("self", 1 if bool(getattr(self, "_mgl_self_shadows_enabled", True)) else 0),
            ("two_sided", 1 if bool(getattr(self, "_mgl_two_sided_shadows_enabled", True)) else 0),
            ("frame", int(frame)),
        ]
        try:
            c = np.asarray(center, dtype=np.float32).reshape(-1)[:3]
            parts.append(("center", tuple(round(float(v), 3) for v in c)))
        except Exception:
            parts.append(("center", (0.0, 0.0, 0.0)))
        try:
            parts.append(("radius", round(float(radius), 3)))
        except Exception:
            parts.append(("radius", 1.0))
        try:
            parts.append(("light_owner", str(getattr(self, "_mgl_scene_light_owner", "") or "")))
            parts.append(("light_type", str(getattr(self, "_mgl_scene_light_type", "directional") or "directional")))
            parts.append(("light_pos", tuple(round(float(v), 5) for v in self._mgl_light_position_tuple())))
            parts.append(("light_dir", tuple(round(float(v), 5) for v in self._mgl_light_direction_tuple())))
            parts.append(("light_intensity", round(float(self._mgl_effective_light_intensity()), 5)))
            parts.append(("light_range", round(float(self._mgl_light_range_value()), 5)))
            parts.append(("light_shadow", round(float(self._mgl_effective_shadow_darkness()), 5)))
            parts.append(("shadow_bias", round(float(self._mgl_effective_shadow_bias()), 7)))
            parts.append(("shadow_range", round(float(getattr(self, "_mgl_scene_light_shadow_range", 0.0) or 0.0), 5)))
            parts.append(("shadow_fov", round(float(getattr(self, "_mgl_scene_light_shadow_fov", 0.0) or 0.0), 5)))
            parts.append(("shadow_near", round(float(getattr(self, "_mgl_scene_light_shadow_near", 0.0) or 0.0), 5)))
        except Exception:
            pass

        visibility = getattr(self, "_mgl_scene_visibility", {}) or {}
        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            try:
                for item in sorted(scene.items(), key=lambda it: (str(getattr(it, "tag", "") or ""), str(getattr(it, "name", "") or ""))):
                    if not bool(getattr(item, "visible", False)):
                        continue
                    payload = getattr(item, "payload", None) or {}
                    owner = str(payload.get("owner") or payload.get("node") or item.name or "").strip()
                    if owner and not bool(visibility.get(owner, True)):
                        continue
                    tag = str(getattr(item, "tag", "") or "")
                    try:
                        bmin, bmax = self.get_scene_owner_bounds(owner) if owner else (None, None)
                        bmin = np.asarray(bmin, dtype=np.float32).reshape(-1)[:3]
                        bmax = np.asarray(bmax, dtype=np.float32).reshape(-1)[:3]
                        b = (
                            tuple(round(float(v), 3) for v in bmin),
                            tuple(round(float(v), 3) for v in bmax),
                        )
                    except Exception:
                        b = None
                    model_sig = None
                    try:
                        model = payload.get("model")
                        if model is not None:
                            model_arr = np.asarray(model, dtype=np.float32).reshape(4, 4)
                            model_sig = tuple(round(float(v), 5) for v in model_arr.reshape(-1))
                    except Exception:
                        model_sig = None
                    xform_sig = None
                    if model_sig is None and owner:
                        try:
                            xf = self._mgl_get_scene_asset_xform(owner)
                            if isinstance(xf, dict):
                                pos = tuple(round(float(v), 5) for v in list(xf.get("pos", (0.0, 0.0, 0.0)))[:3])
                                rot = tuple(round(float(v), 5) for v in list(xf.get("rot", (0.0, 0.0, 0.0)))[:3])
                                scl = tuple(round(float(v), 5) for v in list(xf.get("scl", (1.0, 1.0, 1.0)))[:3])
                                xform_sig = (pos, rot, scl)
                        except Exception:
                            xform_sig = None
                    parts.append(("item", tag, owner, b, model_sig, xform_sig))
            except Exception:
                pass
        try:
            splats_cpu = getattr(self, "_mgl_splats15_cpu", None)
            if splats_cpu is not None:
                parts.append(("splats", int(getattr(self, "_mgl_splat_count", 0) or 0)))
        except Exception:
            pass
        return tuple(parts)

    def _mgl_apply_shadow_uniforms(self, prog) -> None:
        if prog is None or np is None:
            return
        light_dir = self._mgl_light_direction_tuple()
        try:
            prog["LightDir"].value = light_dir
        except Exception:
            pass
        light_type = "directional"
        try:
            light_type = self._mgl_normalize_light_type(getattr(self, "_mgl_scene_light_type", "directional"))
            prog["LightType"].value = int(self._mgl_light_type_index(light_type))
        except Exception:
            pass
        try:
            prog["LightPos"].value = self._mgl_light_position_tuple()
        except Exception:
            pass
        try:
            prog["LightRange"].value = float(self._mgl_light_range_value())
        except Exception:
            pass
        spot_inner_cos, spot_outer_cos = self._mgl_spot_cone_cosines()
        try:
            prog["SpotCosInner"].value = float(spot_inner_cos)
        except Exception:
            pass
        try:
            prog["SpotCosOuter"].value = float(spot_outer_cos)
        except Exception:
            pass
        try:
            prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
        except Exception:
            pass
        try:
            prog["AmbientLight"].value = float(self._mgl_effective_ambient_light())
        except Exception:
            pass
        light_mvp = getattr(self, "_mgl_shadow_light_mvp", None)
        if light_mvp is None:
            light_mvp = np.eye(4, dtype="f4")
        try:
            prog["LightMvp"].write(np.asarray(light_mvp, dtype="f4").tobytes())
        except Exception:
            pass
        point_faces = getattr(self, "_mgl_shadow_light_mvp_faces", None)
        use_point_atlas = bool(light_type == "point" and isinstance(point_faces, list) and len(point_faces) >= 6)
        try:
            prog["UsePointShadowAtlas"].value = 1 if use_point_atlas else 0
        except Exception:
            pass
        ident = np.eye(4, dtype="f4")
        for idx in range(6):
            try:
                mvp = point_faces[idx] if use_point_atlas else ident
                prog[f"PointShadowMvp{idx}"].write(np.asarray(mvp, dtype="f4").tobytes())
            except Exception:
                pass
        use_shadows = bool(
            getattr(self, "_mgl_shadows_enabled", True)
            and getattr(self, "_mgl_shadow_valid", False)
            and getattr(self, "_mgl_shadow_depth_tex", None) is not None
        )
        try:
            prog["UseShadows"].value = 1 if use_shadows else 0
        except Exception:
            pass
        try:
            prog["ShadowBias"].value = float(self._mgl_effective_shadow_bias())
        except Exception:
            pass
        try:
            prog["ShadowDarkness"].value = float(self._mgl_effective_shadow_darkness())
        except Exception:
            pass
        try:
            id_tex = getattr(self, "_mgl_shadow_id_tex", None)
            self_shadows = bool(getattr(self, "_mgl_self_shadows_enabled", True)) or id_tex is None
            prog["UseSelfShadows"].value = 1 if self_shadows else 0
        except Exception:
            pass
        try:
            shadow_width, shadow_height = self._mgl_shadow_texture_dimensions()
        except Exception:
            shadow_width = shadow_height = self._mgl_shadow_map_size_value()
        try:
            prog["ShadowMapSize"].value = (float(shadow_width), float(shadow_height))
        except Exception:
            pass
        if use_shadows:
            try:
                prog["ShadowMap"].value = 7
            except Exception:
                pass
            try:
                prog["ShadowIdMap"].value = 8
            except Exception:
                pass
            try:
                tex = getattr(self, "_mgl_shadow_depth_tex", None)
                if tex is not None:
                    tex.use(location=7)
            except Exception:
                pass
            try:
                id_tex = getattr(self, "_mgl_shadow_id_tex", None)
                if id_tex is not None:
                    id_tex.use(location=8)
            except Exception:
                pass

    def _mgl_shadow_vao_for_entry(self, item: MGLSceneItem, entry: dict, *, instanced: bool = False):
        if self._mgl_ctx is None or getattr(self, "_mgl_shadow_prog", None) is None or not isinstance(entry, dict):
            return None
        key = "_shadow_instanced_vao" if instanced else "_shadow_vao"
        vao = entry.get(key)
        if vao is not None:
            return vao
        vbo = entry.get("vbo")
        ibo = entry.get("ibo")
        if vbo is None:
            return None
        instance_buffers = entry.get("_instance_buffers") if instanced else None
        try:
            vao = self._mgl_ctx.vertex_array(
                self._mgl_shadow_prog,
                self._mgl_shadow_vao_content(vbo, instance_buffers),
                ibo,
                4,
            )
        except Exception:
            return None
        entry[key] = vao
        try:
            resources = getattr(item, "resources", None)
            if isinstance(resources, list):
                resources.append(vao)
        except Exception:
            pass
        return vao

    def _mgl_draw_scene_mesh_shadow(self, item: MGLSceneItem, light_mvp) -> None:
        if self._mgl_ctx is None or getattr(self, "_mgl_shadow_prog", None) is None or item is None:
            return
        payload = getattr(item, "payload", None) or {}
        if self._mgl_scene_item_is_transparent(item):
            return
        if isinstance(payload.get("fbx_rig_context"), dict):
            self._mgl_refresh_fbx_rig_mesh_item(item)
            payload = getattr(item, "payload", None) or {}
        self._mgl_refresh_music_effects_mesh_item(item)
        payload = getattr(item, "payload", None) or {}
        submeshes = [sub for sub in list(payload.get("submeshes") or []) if isinstance(sub, dict)]
        entry = payload.get("mesh_entry") if isinstance(payload.get("mesh_entry"), dict) else None
        if entry is None and payload.get("vao") is not None:
            # Older scene items did not keep a named mesh entry in payload.
            # New loads do, so this path only skips legacy items safely.
            entry = None
        if not submeshes and entry is None:
            return
        try:
            instance_count = max(1, int(payload.get("instance_count", 1) or 1))
        except Exception:
            instance_count = 1
        use_instancing = bool(payload.get("gpu_instancing", False)) and instance_count > 0
        model_np = np.eye(4, dtype="f4")
        try:
            model = payload.get("model")
            if model is not None:
                model_np = np.asarray(model, dtype="f4")
                if getattr(model_np, "shape", None) != (4, 4):
                    model_np = np.eye(4, dtype="f4")
        except Exception:
            model_np = np.eye(4, dtype="f4")
        try:
            self._mgl_shadow_prog["LightMvp"].write(np.asarray(light_mvp, dtype="f4").tobytes())
            self._mgl_shadow_prog["Model"].write(model_np.astype("f4", copy=False).tobytes())
            self._mgl_shadow_prog["UseInstancing"].value = 1 if use_instancing else 0
            shadow_id = max(0, min(65535, int(getattr(item, "item_id", 0) or 0))) / 65535.0
            self._mgl_shadow_prog["ShadowCasterId"].value = float(shadow_id)
        except Exception:
            pass
        if submeshes:
            for sub in submeshes:
                vao = self._mgl_shadow_vao_for_entry(item, sub, instanced=use_instancing)
                if vao is not None:
                    try:
                        if use_instancing:
                            vao.render(instances=instance_count)
                        else:
                            vao.render()
                    except Exception:
                        pass
        elif entry is not None:
            vao = self._mgl_shadow_vao_for_entry(item, entry, instanced=use_instancing)
            if vao is not None:
                try:
                    if use_instancing:
                        vao.render(instances=instance_count)
                    else:
                        vao.render()
                except Exception:
                    pass

    def _mgl_draw_splat_shadow(self, light_proj, light_view) -> None:
        if not bool(getattr(self, "_mgl_splat_cast_shadows_enabled", True)):
            return
        if not bool(getattr(self, "_mgl_render_splats", False)):
            return
        if self._mgl_ctx is None or getattr(self, "_mgl_splat_shadow_prog", None) is None:
            return
        vao = getattr(self, "_mgl_splat_shadow_vao", None)
        count = int(getattr(self, "_mgl_splat_count", 0) or 0)
        if vao is None or count <= 0:
            return
        try:
            self._mgl_splat_shadow_prog["LightProj"].write(np.asarray(light_proj, dtype="f4").tobytes())
            self._mgl_splat_shadow_prog["LightView"].write(np.asarray(light_view, dtype="f4").tobytes())
            self._mgl_splat_shadow_prog["SplatWorldScale"].value = float(getattr(self, "_mgl_splat_world_scale", 1.0))
            self._mgl_splat_shadow_prog["ShadowCasterId"].value = 0.0
        except Exception:
            pass
        try:
            vao.render(
                mode=moderngl.TRIANGLE_STRIP,
                vertices=4,
                instances=count,
            )
        except Exception:
            pass

    def _mgl_render_shadow_map(self) -> None:
        if not bool(getattr(self, "_mgl_shadows_enabled", True)):
            try:
                self._mgl_shadow_valid = False
            except Exception:
                pass
            return
        if self._mgl_ctx is None or not self._mgl_ensure_shadow_resources():
            try:
                self._mgl_shadow_valid = False
            except Exception:
                pass
            return
        matrices = self._mgl_shadow_light_matrices()
        if matrices is None:
            try:
                self._mgl_shadow_valid = False
            except Exception:
                pass
            return
        light_proj, light_view, light_mvp, shadow_center, shadow_radius = matrices
        fbo = getattr(self, "_mgl_shadow_fbo", None)
        if fbo is None:
            try:
                self._mgl_shadow_valid = False
            except Exception:
                pass
            return
        texture_size = self._mgl_shadow_texture_dimensions()
        signature = self._mgl_shadow_scene_signature(texture_size, shadow_center, shadow_radius)
        explicit_update = bool(getattr(self, "_mgl_shadow_dirty", True) or getattr(self, "_mgl_shadow_render_active", False))
        force_update = bool(explicit_update)
        if not force_update:
            try:
                force_update = signature != getattr(self, "_mgl_shadow_signature", None)
            except Exception:
                force_update = True
        if force_update and not explicit_update:
            try:
                interval = float(self._mgl_shadow_update_interval_value())
                now = time.perf_counter()
                last = float(getattr(self, "_mgl_shadow_last_update_ts", 0.0) or 0.0)
                if interval > 0.0 and (now - last) < interval:
                    force_update = False
            except Exception:
                pass
        if not force_update and bool(getattr(self, "_mgl_shadow_valid", False)):
            try:
                self._mgl_shadow_light_mvp = np.asarray(light_mvp, dtype="f4")
            except Exception:
                pass
            return
        old_viewport = None
        old_scissor = None
        old_depth_mask = None
        old_depth_func = None
        old_wireframe = None
        try:
            old_viewport = self._mgl_ctx.viewport
        except Exception:
            old_viewport = None
        try:
            old_scissor = self._mgl_ctx.scissor
        except Exception:
            old_scissor = None
        try:
            old_depth_mask = getattr(self._mgl_ctx, "depth_mask", None)
        except Exception:
            old_depth_mask = None
        try:
            old_depth_func = getattr(self._mgl_ctx, "depth_func", None)
        except Exception:
            old_depth_func = None
        try:
            old_wireframe = bool(getattr(self._mgl_ctx, "wireframe", False))
        except Exception:
            old_wireframe = None
        try:
            fbo.use()
            try:
                raw_gl = getattr(self, "_gl", None)
                glo = int(getattr(fbo, "glo", 0) or 0)
                if raw_gl is not None and glo > 0 and hasattr(raw_gl, "glBindFramebuffer"):
                    raw_gl.glBindFramebuffer(0x8D40, glo)  # GL_FRAMEBUFFER
                if raw_gl is not None and getattr(self, "_mgl_shadow_id_tex", None) is not None:
                    if hasattr(raw_gl, "glColorMask"):
                        raw_gl.glColorMask(True, True, True, True)
                    if hasattr(raw_gl, "glDrawBuffer"):
                        raw_gl.glDrawBuffer(0x8CE0)  # GL_COLOR_ATTACHMENT0
            except Exception:
                pass
            self._mgl_ctx.viewport = (0, 0, int(texture_size[0]), int(texture_size[1]))
            try:
                # Film-gate camera views use scissor on the main pass; shadow maps must render unclipped.
                self._mgl_ctx.scissor = None
            except Exception:
                pass
            try:
                if getattr(self, "_mgl_shadow_id_tex", None) is not None:
                    fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
                else:
                    fbo.clear(depth=1.0)
            except Exception:
                try:
                    if getattr(self, "_mgl_shadow_id_tex", None) is not None:
                        self._mgl_ctx.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
                    else:
                        self._mgl_ctx.clear(depth=1.0)
                except Exception:
                    pass
            try:
                self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                self._mgl_ctx.disable(moderngl.BLEND)
                if bool(getattr(self, "_mgl_two_sided_shadows_enabled", True)):
                    self._mgl_ctx.disable(moderngl.CULL_FACE)
                else:
                    self._mgl_ctx.enable(moderngl.CULL_FACE)
            except Exception:
                pass
            try:
                self._mgl_ctx.depth_mask = True
                self._mgl_ctx.depth_func = "<="
                self._mgl_ctx.wireframe = False
            except Exception:
                pass
            scene = getattr(self, "_mgl_scene", None)
            point_faces = getattr(self, "_mgl_shadow_point_faces", None)
            if scene is not None:
                try:
                    items = [
                        item
                        for item in sorted(scene.items(), key=lambda it: it.order)
                        if bool(getattr(item, "visible", False))
                    ]
                except Exception:
                    items = []
                if isinstance(point_faces, list) and len(point_faces) >= 6:
                    for face in point_faces[:6]:
                        try:
                            viewport = face.get("viewport", None) if isinstance(face, dict) else None
                            face_mvp = face.get("mvp", light_mvp) if isinstance(face, dict) else light_mvp
                            if viewport is not None:
                                self._mgl_ctx.viewport = tuple(int(v) for v in viewport)
                            for item in items:
                                self._mgl_draw_scene_mesh_shadow(item, face_mvp)
                            if isinstance(face, dict):
                                self._mgl_draw_splat_shadow(face.get("proj", light_proj), face.get("view", light_view))
                        except Exception:
                            pass
                else:
                    self._mgl_ctx.viewport = (0, 0, int(texture_size[0]), int(texture_size[1]))
                    for item in items:
                        self._mgl_draw_scene_mesh_shadow(item, light_mvp)
                    try:
                        self._mgl_draw_splat_shadow(light_proj, light_view)
                    except Exception:
                        pass
            else:
                if isinstance(point_faces, list) and len(point_faces) >= 6:
                    for face in point_faces[:6]:
                        try:
                            viewport = face.get("viewport", None) if isinstance(face, dict) else None
                            if viewport is not None:
                                self._mgl_ctx.viewport = tuple(int(v) for v in viewport)
                            if isinstance(face, dict):
                                self._mgl_draw_splat_shadow(face.get("proj", light_proj), face.get("view", light_view))
                        except Exception:
                            pass
                else:
                    try:
                        self._mgl_draw_splat_shadow(light_proj, light_view)
                    except Exception:
                        pass
            self._mgl_shadow_light_mvp = np.asarray(light_mvp, dtype="f4")
            self._mgl_shadow_signature = signature
            self._mgl_shadow_dirty = False
            self._mgl_shadow_last_update_ts = time.perf_counter()
            self._mgl_shadow_valid = True
        except Exception as exc:
            try:
                self._mgl_log_throttled(
                    "_mgl_shadow_render_error_ts",
                    "shadows: render failed err=" + repr(exc),
                    2.0,
                )
            except Exception:
                pass
        finally:
            try:
                if old_wireframe is not None:
                    self._mgl_ctx.wireframe = old_wireframe
            except Exception:
                pass
            try:
                if old_depth_mask is not None:
                    self._mgl_ctx.depth_mask = old_depth_mask
            except Exception:
                pass
            try:
                if old_depth_func is not None:
                    self._mgl_ctx.depth_func = old_depth_func
            except Exception:
                pass
            try:
                self._mgl_ctx.enable(moderngl.BLEND | moderngl.DEPTH_TEST)
                if bool(getattr(self, "_mgl_cull_enabled", False)):
                    self._mgl_ctx.enable(moderngl.CULL_FACE)
                else:
                    self._mgl_ctx.disable(moderngl.CULL_FACE)
            except Exception:
                pass
            try:
                self._mgl_bind_default_fbo()
            except Exception:
                pass
            try:
                if int(self._mgl_target_framebuffer_id()) <= 0 and hasattr(self._mgl_ctx, "screen"):
                    self._mgl_ctx.screen.use()
            except Exception:
                pass
            try:
                if old_viewport is not None:
                    self._mgl_ctx.viewport = old_viewport
                else:
                    w, h = self._mgl_render_size()
                    self._mgl_ctx.viewport = (0, 0, int(w), int(h))
            except Exception:
                pass
            try:
                self._mgl_ctx.scissor = old_scissor
            except Exception:
                pass

    def _mgl_clamped_offscreen_render_size(self, width: int, height: int) -> Tuple[int, int]:
        w = max(2, int(width))
        h = max(2, int(height))
        max_w = 0
        max_h = 0
        try:
            info = getattr(self._mgl_ctx, "info", {}) if getattr(self, "_mgl_ctx", None) is not None else {}
            if isinstance(info, dict):
                viewport = info.get("GL_MAX_VIEWPORT_DIMS", None)
                if isinstance(viewport, (list, tuple)) and len(viewport) >= 2:
                    max_w = max(max_w, int(viewport[0]))
                    max_h = max(max_h, int(viewport[1]))
                for key in ("GL_MAX_TEXTURE_SIZE", "GL_MAX_RENDERBUFFER_SIZE"):
                    limit = int(info.get(key, 0) or 0)
                    if limit > 0:
                        max_w = limit if max_w <= 0 else min(max_w, limit)
                        max_h = limit if max_h <= 0 else min(max_h, limit)
        except Exception:
            max_w = max_h = 0
        if max_w > 1 and max_h > 1 and (w > max_w or h > max_h):
            scale = min(float(max_w) / float(max(1, w)), float(max_h) / float(max(1, h)))
            if scale > 0.0:
                w = max(2, int(math.floor(float(w) * scale)))
                h = max(2, int(math.floor(float(h) * scale)))
        return int(w), int(h)

    def _mgl_render_to_image(self, width: int, height: int) -> Optional[QtGui.QImage]:
        if (not _HAS_MGL) or getattr(self, "_mgl_ctx", None) is None:
            return None
        w, h = self._mgl_clamped_offscreen_render_size(width, height)
        fbo = None
        prev_size_override = getattr(self, "_mgl_render_size_override", None)
        prev_target_fbo = int(getattr(self, "_mgl_render_target_fbo_id", 0) or 0)
        prev_render_paused = bool(getattr(self, "_render_paused", False))
        prev_shadow_render_active = bool(getattr(self, "_mgl_shadow_render_active", False))
        prev_shadow_dirty = bool(getattr(self, "_mgl_shadow_dirty", True))
        prev_viewport = None
        prev_scissor = None
        try:
            prev_splat_min_dt = float(getattr(self, "_mgl_splats_rebuild_min_dt", 0.05) or 0.05)
        except Exception:
            prev_splat_min_dt = 0.05
        try:
            try:
                if hasattr(self, "makeCurrent"):
                    self.makeCurrent()
            except Exception:
                pass
            try:
                prev_viewport = self._mgl_ctx.viewport
            except Exception:
                prev_viewport = None
            try:
                prev_scissor = self._mgl_ctx.scissor
            except Exception:
                prev_scissor = None

            fbo = self._mgl_get_render_offscreen_fbo(w, h)
            if fbo is None:
                return None
            self._mgl_render_size_override = (w, h)
            try:
                self._mgl_render_target_fbo_id = int(getattr(fbo, "glo", 0) or 0)
            except Exception:
                self._mgl_render_target_fbo_id = 0
            if int(getattr(self, "_mgl_render_target_fbo_id", 0) or 0) <= 0:
                return None

            # Reuse the normal render path, but redirect framebuffer binding
            # to the offscreen target to keep behavior consistent across frames.
            try:
                fbo.use()
            except Exception:
                pass
            try:
                self._mgl_ctx.viewport = (0, 0, int(w), int(h))
                self._mgl_ctx.scissor = None
            except Exception:
                pass
            try:
                if self._gl is not None:
                    self._gl.glBindFramebuffer(0x8D40, int(getattr(fbo, "glo", 0) or 0))  # GL_FRAMEBUFFER
                    self._gl.glViewport(0, 0, int(w), int(h))
                    self._gl.glDisable(GL_SCISSOR_TEST)
            except Exception:
                pass
            self._render_paused = False
            try:
                self._mgl_shadow_render_active = True
                self._mgl_shadow_dirty = True
            except Exception:
                pass
            try:
                self._mgl_splats_rebuild_min_dt = 0.0
            except Exception:
                pass
            self._paint_mgl()
            try:
                if hasattr(self._mgl_ctx, "finish"):
                    self._mgl_ctx.finish()
            except Exception:
                pass

            data = None
            try:
                tex = getattr(self, "_mgl_render_offscreen_tex", None)
                if tex is not None:
                    raw = tex.read(alignment=1)
                    if raw and len(raw) == int(w) * int(h) * 4:
                        data = raw
            except Exception:
                data = None
            if not data:
                try:
                    data = fbo.read(viewport=(0, 0, int(w), int(h)), components=4, alignment=1)
                except TypeError:
                    data = fbo.read(components=4, alignment=1)
            if not data:
                return None
            if hasattr(QtGui.QImage, "Format_RGBA8888"):
                fmt = QtGui.QImage.Format_RGBA8888
            else:
                fmt = QtGui.QImage.Format_ARGB32
            image = QtGui.QImage(data, w, h, w * 4, fmt)
            if image.isNull():
                return None
            return image.mirrored(False, True).copy()
        except Exception:
            return None
        finally:
            try:
                self._mgl_render_size_override = prev_size_override
            except Exception:
                pass
            try:
                self._mgl_render_target_fbo_id = int(prev_target_fbo)
            except Exception:
                self._mgl_render_target_fbo_id = 0
            try:
                self._render_paused = bool(prev_render_paused)
            except Exception:
                self._render_paused = False
            try:
                self._mgl_shadow_render_active = bool(prev_shadow_render_active)
                self._mgl_shadow_dirty = bool(prev_shadow_dirty)
            except Exception:
                pass
            try:
                self._mgl_splats_rebuild_min_dt = float(prev_splat_min_dt)
            except Exception:
                self._mgl_splats_rebuild_min_dt = 0.05
            try:
                self._mgl_bind_default_fbo()
            except Exception:
                pass
            try:
                if hasattr(self._mgl_ctx, "screen"):
                    self._mgl_ctx.screen.use()
            except Exception:
                pass
            try:
                if prev_viewport is not None:
                    self._mgl_ctx.viewport = prev_viewport
            except Exception:
                pass
            try:
                self._mgl_ctx.scissor = prev_scissor
            except Exception:
                pass
            try:
                if hasattr(self, "doneCurrent"):
                    self.doneCurrent()
            except Exception:
                pass

    def _mgl_get_render_offscreen_fbo(self, width: int, height: int):
        if (not _HAS_MGL) or getattr(self, "_mgl_ctx", None) is None:
            return None
        w = max(2, int(width))
        h = max(2, int(height))
        cur_size = getattr(self, "_mgl_render_offscreen_size", None)
        fbo = getattr(self, "_mgl_render_offscreen_fbo", None)
        tex = getattr(self, "_mgl_render_offscreen_tex", None)
        depth = getattr(self, "_mgl_render_offscreen_depth", None)
        if (
            isinstance(cur_size, (list, tuple))
            and len(cur_size) >= 2
            and int(cur_size[0]) == w
            and int(cur_size[1]) == h
            and fbo is not None
            and tex is not None
            and depth is not None
        ):
            return fbo
        try:
            if fbo is not None:
                fbo.release()
        except Exception:
            pass
        try:
            if depth is not None:
                depth.release()
        except Exception:
            pass
        try:
            if tex is not None:
                tex.release()
        except Exception:
            pass
        try:
            tex = self._mgl_ctx.texture((w, h), 4)
            depth = self._mgl_ctx.depth_renderbuffer((w, h))
            fbo = self._mgl_ctx.framebuffer(color_attachments=[tex], depth_attachment=depth)
        except Exception:
            return None
        self._mgl_render_offscreen_size = (w, h)
        self._mgl_render_offscreen_tex = tex
        self._mgl_render_offscreen_depth = depth
        self._mgl_render_offscreen_fbo = fbo
        return fbo

    def _mgl_get_material_refraction_fbo(self, width: int, height: int):
        if (not _HAS_MGL) or getattr(self, "_mgl_ctx", None) is None:
            return None
        w = max(2, int(width))
        h = max(2, int(height))
        cur_size = getattr(self, "_mgl_material_scene_tex_size", None)
        tex = getattr(self, "_mgl_material_scene_tex", None)
        fbo = getattr(self, "_mgl_material_scene_fbo", None)
        if (
            isinstance(cur_size, (list, tuple))
            and len(cur_size) >= 2
            and int(cur_size[0]) == w
            and int(cur_size[1]) == h
            and tex is not None
            and fbo is not None
        ):
            return fbo
        try:
            if fbo is not None:
                fbo.release()
        except Exception:
            pass
        try:
            if tex is not None:
                tex.release()
        except Exception:
            pass
        try:
            tex = self._mgl_ctx.texture((w, h), 4)
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = False
            tex.repeat_y = False
            fbo = self._mgl_ctx.framebuffer(color_attachments=[tex])
        except Exception:
            return None
        self._mgl_material_scene_fbo = fbo
        self._mgl_material_scene_tex = tex
        self._mgl_material_scene_tex_size = (w, h)
        return fbo

    def _mgl_capture_material_refraction_scene(self) -> bool:
        if (not _HAS_MGL) or getattr(self, "_mgl_ctx", None) is None:
            return False
        try:
            w, h = self._mgl_render_size()
        except Exception:
            w, h = (2, 2)
        scale = float(getattr(self, "_mgl_material_refraction_scale", 0.5) or 0.5)
        if scale <= 0.0:
            scale = 0.5
        tex_w = max(2, int(round(w * scale)))
        tex_h = max(2, int(round(h * scale)))
        dst_fbo = self._mgl_get_material_refraction_fbo(tex_w, tex_h)
        if dst_fbo is None:
            return False
        src_fbo = None
        try:
            target_fbo = int(self._mgl_target_framebuffer_id())
        except Exception:
            target_fbo = 0
        if target_fbo > 0 and hasattr(self._mgl_ctx, "detect_framebuffer"):
            try:
                src_fbo = self._mgl_ctx.detect_framebuffer(target_fbo)
            except Exception:
                src_fbo = None
        if src_fbo is None:
            try:
                src_fbo = getattr(self._mgl_ctx, "fbo", None) or getattr(self._mgl_ctx, "screen", None)
            except Exception:
                src_fbo = None
        if src_fbo is None:
            return False
        try:
            self._mgl_ctx.copy_framebuffer(dst_fbo, src_fbo)
            try:
                self._mgl_bind_default_fbo()
            except Exception:
                pass
            try:
                self._mgl_ctx.viewport = (0, 0, int(w), int(h))
            except Exception:
                pass
            try:
                if self._gl is not None:
                    target_fbo = int(self._mgl_target_framebuffer_id())
                    if target_fbo > 0:
                        self._gl.glBindFramebuffer(0x8D40, target_fbo)  # GL_FRAMEBUFFER
                    self._gl.glViewport(0, 0, int(w), int(h))
            except Exception:
                pass
            self._mgl_material_scene_screen_size = (float(w), float(h))
            self._mgl_material_scene_valid = True
            self._mgl_material_log_throttled(
                "refraction-capture",
                "renderer.refraction_capture",
                interval=1.0,
                screen_size=[int(w), int(h)],
                texture_size=[int(tex_w), int(tex_h)],
            )
            return True
        except Exception as exc:
            pass
        raw_gl = getattr(self, "_gl", None)
        if raw_gl is not None and hasattr(raw_gl, "glBlitFramebuffer"):
            try:
                src_glo = int(getattr(src_fbo, "glo", 0) or 0)
            except Exception:
                src_glo = 0
            try:
                dst_glo = int(getattr(dst_fbo, "glo", 0) or 0)
            except Exception:
                dst_glo = 0
            if src_glo > 0 and dst_glo > 0:
                try:
                    raw_gl.glBindFramebuffer(0x8CA8, src_glo)  # GL_READ_FRAMEBUFFER
                    raw_gl.glBindFramebuffer(0x8CA9, dst_glo)  # GL_DRAW_FRAMEBUFFER
                    raw_gl.glBlitFramebuffer(
                        0,
                        0,
                        int(w),
                        int(h),
                        0,
                        0,
                        int(tex_w),
                        int(tex_h),
                        GL_COLOR_BUFFER_BIT,
                        0x2601,  # GL_LINEAR
                    )
                    self._mgl_bind_default_fbo()
                    try:
                        self._mgl_ctx.viewport = (0, 0, int(w), int(h))
                    except Exception:
                        pass
                    try:
                        if self._gl is not None:
                            target_fbo = int(self._mgl_target_framebuffer_id())
                            if target_fbo > 0:
                                self._gl.glBindFramebuffer(0x8D40, target_fbo)  # GL_FRAMEBUFFER
                            self._gl.glViewport(0, 0, int(w), int(h))
                    except Exception:
                        pass
                    self._mgl_material_scene_screen_size = (float(w), float(h))
                    self._mgl_material_scene_valid = True
                    self._mgl_material_log_throttled(
                        "refraction-capture-raw",
                        "renderer.refraction_capture_raw",
                        interval=1.0,
                        screen_size=[int(w), int(h)],
                        texture_size=[int(tex_w), int(tex_h)],
                    )
                    return True
                except Exception as raw_exc:
                    exc = raw_exc
                finally:
                    try:
                        self._mgl_bind_default_fbo()
                    except Exception:
                        pass
        self._mgl_material_scene_valid = False
        self._mgl_material_log_throttled(
            "refraction-capture-error",
            "renderer.refraction_capture_error",
            interval=2.0,
            error=repr(exc),
        )
        return False

    def _paint_mgl_draw_grid_pass(self, *, mvp) -> None:
        retarget_grid_size = 0.0
        try:
            retarget_grid_size = float(self._mgl_retarget_grid_min_render_size())
        except Exception:
            retarget_grid_size = 0.0
        try:
            current_grid_size = float(getattr(self, "_mgl_grid_render_size", 0.0) or 0.0)
        except Exception:
            current_grid_size = 0.0
        if bool(getattr(self, "_mgl_grid_visible", False)) and (
            self._mgl_grid_vao is None or not bool(getattr(self, "_mgl_grid_vertex_count", 0))
            or (retarget_grid_size > 0.0 and current_grid_size < retarget_grid_size * 0.95)
        ):
            try:
                self._mgl_update_grid()
            except Exception:
                pass
        try:
            if bool(getattr(self, "_mgl_grid_visible", False)):
                if self._mgl_grid_vao is None:
                    self._mgl_log_throttled(
                        "_mgl_grid_missing_ts",
                        "grid: visible but vao=None count=" + str(getattr(self, "_mgl_grid_vertex_count", 0)),
                        1.0,
                    )
                elif not bool(getattr(self, "_mgl_grid_vertex_count", 0)):
                    self._mgl_log_throttled(
                        "_mgl_grid_empty_ts",
                        "grid: visible but count=0 vao=" + str(self._mgl_grid_vao is not None),
                        1.0,
                    )
                else:
                    pass
        except Exception:
            pass
        if bool(getattr(self, "_mgl_grid_visible", False)) and self._mgl_grid_vao is not None:
            try:
                try:
                    _prev_depth_mask = bool(getattr(self._mgl_ctx, "depth_mask", True))
                except Exception:
                    _prev_depth_mask = True
                try:
                    _prev_lw = float(getattr(self._mgl_ctx, "line_width", 1.0))
                except Exception:
                    _prev_lw = 1.0
                try:
                    _prev_depth_func = getattr(self._mgl_ctx, "depth_func", None)
                except Exception:
                    _prev_depth_func = None

                prev_depth_test = True
                try:
                    prev_depth_test = bool(getattr(self._mgl_ctx, "depth_test", True))
                except Exception:
                    prev_depth_test = True
                try:
                    self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                except Exception:
                    pass
                try:
                    self._mgl_ctx.depth_func = "<="
                except Exception:
                    pass
                try:
                    self._mgl_ctx.depth_mask = False
                except Exception:
                    pass
                try:
                    self._mgl_ctx.line_width = 1.6
                except Exception:
                    pass
                try:
                    self._mgl_ctx.enable(moderngl.BLEND)
                except Exception:
                    pass
                try:
                    self._mgl_ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
                except Exception:
                    pass

                try:
                    grid_alpha = float(getattr(self, "_mgl_grid_alpha", 0.35))
                except Exception:
                    grid_alpha = 0.35
                if not math.isfinite(grid_alpha):
                    grid_alpha = 0.35
                grid_alpha = max(0.2, min(1.0, grid_alpha))
                grid_rgb = (0.35, 0.35, 0.35)

                spacing = 1.0
                render_size = 0.0
                render_steps = 0
                try:
                    render_steps = int(getattr(self, "_mgl_grid_render_steps", 0))
                except Exception:
                    render_steps = 0
                try:
                    render_size = float(getattr(self, "_mgl_grid_render_size", 0.0))
                except Exception:
                    render_size = 0.0
                try:
                    spacing = float(getattr(self, "_mgl_grid_spacing", 0.0))
                except Exception:
                    spacing = 0.0
                if render_steps <= 0:
                    try:
                        vcount = int(getattr(self, "_mgl_grid_vertex_count", 0))
                        if vcount > 0:
                            render_steps = max(1, vcount // 4)
                    except Exception:
                        render_steps = 0
                if render_steps > 0 and spacing <= 0.0:
                    denom = max(1, render_steps - 1)
                    spacing = float((max(render_size, 0.0) * 2.0) / float(denom)) if render_size > 0.0 else 1.0

                major_step = 10.0
                major_boost = 1.15
                try:
                    major_step = float(getattr(self, "_mgl_grid_major_step", major_step))
                except Exception:
                    pass
                try:
                    major_boost = float(getattr(self, "_mgl_grid_major_boost", major_boost))
                except Exception:
                    pass

                fade_start = 0.0
                fade_end = 0.0
                zoom_scale = 1.0
                grid_fx = bool(getattr(self, "_mgl_grid_fx_enabled", True))
                cam_height = None
                try:
                    fade_start_frac = float(getattr(self, "_mgl_grid_fade_start", 0.55))
                except Exception:
                    fade_start_frac = 0.55
                try:
                    fade_end_frac = float(getattr(self, "_mgl_grid_fade_end", 0.95))
                except Exception:
                    fade_end_frac = 0.95
                try:
                    zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
                except Exception:
                    zoom = 0.0
                try:
                    base_zoom = float(getattr(self, "_mgl_base_zoom", zoom))
                except Exception:
                    base_zoom = zoom
                if not math.isfinite(base_zoom) or base_zoom <= 0.0:
                    base_zoom = max(1e-6, zoom)
                if zoom > 0.0 and base_zoom > 0.0:
                    zoom_scale = zoom / base_zoom
                try:
                    cam_world = getattr(self, "_mgl_cam_world", None)
                    if cam_world is not None:
                        cam_height = abs(float(cam_world[1]))
                except Exception:
                    cam_height = None
                if render_size > 0.0:
                    max_fade = render_size * 0.98
                    retarget_grid = bool(getattr(self, "_mgl_retarget_preview_active", False))
                    if not grid_fx:
                        fade_start = 0.0
                        fade_end = 0.0
                    elif retarget_grid:
                        fade_start = render_size * 0.64
                        fade_end = max_fade
                    else:
                        fade_start = render_size * max(0.0, min(1.0, fade_start_frac))
                        fade_end = render_size * max(0.0, min(1.0, fade_end_frac))
                        try:
                            cam_dist = float(getattr(self, "_mgl_camera_zoom", 0.0))
                        except Exception:
                            cam_dist = 0.0
                        if cam_dist > 0.0:
                            fade_end = cam_dist * 2.4
                            fade_start = max(0.0, fade_end * 0.35)
                        zoom_scale = max(1e-6, zoom_scale)
                        try:
                            zoom_exp = float(getattr(self, "_mgl_grid_fade_zoom_exp", 1.1))
                        except Exception:
                            zoom_exp = 1.1
                        if zoom_exp <= 0.0:
                            zoom_exp = 1.0
                        zoom_scale = zoom_scale ** zoom_exp
                        fade_start *= zoom_scale
                        fade_end *= zoom_scale
                        if cam_height is not None:
                            try:
                                height_ref = float(getattr(self, "_mgl_grid_fade_height", 5.0))
                            except Exception:
                                height_ref = 5.0
                            try:
                                height_boost = float(getattr(self, "_mgl_grid_fade_low_boost", 1.0))
                            except Exception:
                                height_boost = 1.0
                            if height_ref > 0.0 and height_boost > 0.0:
                                height_scale = 1.0 + (height_boost * math.exp(-cam_height / height_ref))
                                fade_start *= height_scale
                                fade_end *= height_scale
                    if grid_fx and fade_end > max_fade:
                        if fade_end > 1e-6:
                            scale = max_fade / fade_end
                            fade_start *= scale
                        fade_end = max_fade
                    if fade_end <= fade_start:
                        fade_start = 0.0
                        fade_end = 0.0
                try:
                    self._mgl_grid_fade_start_current = float(fade_start)
                    self._mgl_grid_fade_end_current = float(fade_end)
                except Exception:
                    pass

                grid_offset_x = 0.0
                grid_offset_z = 0.0
                center = getattr(self, "_mgl_center", None)
                if center is not None:
                    try:
                        grid_offset_x = float(center[0])
                        grid_offset_z = float(center[2])
                    except Exception:
                        grid_offset_x = 0.0
                        grid_offset_z = 0.0
                fade_origin_x = grid_offset_x
                fade_origin_z = grid_offset_z
                if spacing > 1e-6:
                    grid_offset_x = math.floor(grid_offset_x / spacing) * spacing
                    grid_offset_z = math.floor(grid_offset_z / spacing) * spacing

                self._mgl_grid_prog["Mvp"].write(mvp.astype("f4").tobytes())
                try:
                    self._mgl_apply_shadow_uniforms(self._mgl_grid_prog)
                except Exception:
                    pass
                self._mgl_grid_prog["Color"].value = (grid_rgb[0], grid_rgb[1], grid_rgb[2], grid_alpha)
                try:
                    self._mgl_grid_prog["GridSpacing"].value = float(spacing)
                except Exception:
                    pass
                try:
                    self._mgl_grid_prog["MajorStep"].value = float(major_step)
                except Exception:
                    pass
                try:
                    self._mgl_grid_prog["MajorBoost"].value = float(major_boost)
                except Exception:
                    pass
                try:
                    self._mgl_grid_prog["FadeStart"].value = float(fade_start)
                except Exception:
                    pass
                try:
                    self._mgl_grid_prog["FadeEnd"].value = float(fade_end)
                except Exception:
                    pass
                try:
                    self._mgl_grid_prog["GridOffset"].value = (grid_offset_x, grid_offset_z)
                except Exception:
                    pass
                try:
                    self._mgl_grid_prog["FadeOrigin"].value = (fade_origin_x, fade_origin_z)
                except Exception:
                    pass
                try:
                    steps = render_steps
                    if steps <= 0:
                        steps = 1
                    if (steps % 2) == 0:
                        steps += 1
                    mid = steps // 2

                    skip_x = None
                    skip_z = None
                    if spacing > 1e-6 and render_size > 0.0:
                        try:
                            skip_x = int(round((render_size - grid_offset_x) / spacing))
                            if skip_x < 0 or skip_x >= steps:
                                skip_x = None
                        except Exception:
                            skip_x = None
                        try:
                            skip_z = int(round((render_size - grid_offset_z) / spacing))
                            if skip_z < 0 or skip_z >= steps:
                                skip_z = None
                        except Exception:
                            skip_z = None

                    block_verts = steps * 2
                    if skip_x is None:
                        self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=block_verts, first=0)
                    else:
                        before = skip_x * 2
                        after = (steps - skip_x - 1) * 2
                        if before > 0:
                            self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=before, first=0)
                        if after > 0:
                            self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=after, first=(skip_x + 1) * 2)
                    base = block_verts
                    if skip_z is None:
                        self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=block_verts, first=base)
                    else:
                        before = skip_z * 2
                        after = (steps - skip_z - 1) * 2
                        if before > 0:
                            self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=before, first=base)
                        if after > 0:
                            self._mgl_grid_vao.render(
                                mode=moderngl.LINES,
                                vertices=after,
                                first=base + (skip_z + 1) * 2,
                            )

                    try:
                        _center_prev_lw = float(getattr(self._mgl_ctx, "line_width", 1.0))
                    except Exception:
                        _center_prev_lw = 1.0
                    try:
                        center_line_width = float(getattr(self, "_mgl_grid_center_line_width", 3.0) or 3.0)
                        self._mgl_ctx.line_width = max(2.0, center_line_width)
                    except Exception:
                        pass
                    try:
                        center_color = getattr(self, "_mgl_grid_center_color", (1.0, 1.0, 1.0, 0.95))
                        self._mgl_grid_prog["Color"].value = (
                            float(center_color[0]),
                            float(center_color[1]),
                            float(center_color[2]),
                            float(center_color[3]),
                        )
                    except Exception:
                        pass
                    try:
                        self._mgl_grid_prog["MajorBoost"].value = 1.0
                    except Exception:
                        pass
                    try:
                        self._mgl_grid_prog["GridOffset"].value = (0.0, 0.0)
                        self._mgl_grid_prog["FadeOrigin"].value = (fade_origin_x, fade_origin_z)
                        self._mgl_grid_prog["FadeStart"].value = float(fade_start)
                        self._mgl_grid_prog["FadeEnd"].value = float(fade_end)
                    except Exception:
                        pass
                    first_center_z = 2 * mid
                    first_center_x = (steps * 2) + (2 * mid)
                    self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=2, first=first_center_z)
                    self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=2, first=first_center_x)
                    try:
                        self._mgl_ctx.line_width = _center_prev_lw
                    except Exception:
                        pass
                    try:
                        self._mgl_grid_prog["Color"].value = (
                            0.35,
                            0.35,
                            0.35,
                            float(getattr(self, "_mgl_grid_alpha", 0.35)),
                        )
                    except Exception:
                        pass
                    try:
                        self._mgl_grid_prog["MajorBoost"].value = 1.0
                    except Exception:
                        pass
                except Exception:
                    self._mgl_grid_vao.render(moderngl.LINES)

                try:
                    self._mgl_ctx.line_width = _prev_lw
                except Exception:
                    pass
                try:
                    self._mgl_ctx.depth_mask = _prev_depth_mask
                except Exception:
                    pass
                try:
                    if _prev_depth_func is not None:
                        self._mgl_ctx.depth_func = _prev_depth_func
                except Exception:
                    pass
                try:
                    if prev_depth_test:
                        self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                    else:
                        self._mgl_ctx.disable(moderngl.DEPTH_TEST)
                except Exception:
                    pass
            except Exception:
                pass

    def _paint_mgl_draw_splats_pass(self, *, proj, lookat, transform) -> None:
        dbg = bool(getattr(self, "_mgl_debug", False))
        try:
            reason = None
            if not bool(getattr(self, "_mgl_render_splats", False)):
                reason = "render_splats=False"
            elif getattr(self, "_mgl_splatq_vao", None) is None:
                reason = "vao=None"
            elif getattr(self, "_mgl_splatq_prog", None) is None:
                reason = "prog=None"
            elif not bool(getattr(self, "_mgl_splat_count", 0)):
                reason = "count=0"
            last = getattr(self, "_mgl_splat_skip_reason", None)
            if reason != last:
                self._mgl_splat_skip_reason = reason
                if reason:
                    self._mgl_log("splats: draw skip reason=" + str(reason))
                else:
                    self._mgl_log("splats: draw active count=" + str(getattr(self, "_mgl_splat_count", 0)))
        except Exception:
            pass
        try:
            if (
                self._mgl_render_splats
                and self._mgl_splatq_vao is not None
                and self._mgl_splatq_prog is not None
                and self._mgl_splat_count
            ):

                # common state for splats
                self._mgl_ctx.enable(moderngl.BLEND)
                self._mgl_ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA

                # depth test for splats (store previous state so we can restore)
                prev_depth_test = True
                try:
                    prev_depth_test = bool(getattr(self._mgl_ctx, "depth_test", True))
                except Exception:
                    prev_depth_test = True
                prev_depth_func = None
                try:
                    prev_depth_func = getattr(self._mgl_ctx, "depth_func", None)
                except Exception:
                    prev_depth_func = None

                # depth test for splats (toggle from scene node param if available)
                depth_on = True
                try:
                    # this gets set by the Scene node checkbox
                    raw = str(getattr(self, "_mgl_splat_depth_test", "") or "").strip().lower()
                    if raw:
                        depth_on = raw in ("1", "true", "yes", "on")
                except Exception:
                    depth_on = True

                try:
                    if depth_on:
                        self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                    else:
                        self._mgl_ctx.disable(moderngl.DEPTH_TEST)
                except Exception:
                    pass
                try:
                    # Keep splat depth compare deterministic regardless of other passes.
                    self._mgl_ctx.depth_func = "<="
                except Exception:
                    pass

                # disable depth writes (robust: ModernGL + raw GL fallback)
                prev_depth_mask = True
                try:
                    prev_depth_mask = bool(getattr(self._mgl_ctx, "depth_mask", True))
                except Exception:
                    prev_depth_mask = True

                did_set_depth_mask = False
                try:
                    self._mgl_ctx.depth_mask = False
                    did_set_depth_mask = True
                except Exception:
                    # fallback to raw GL (Qt context)
                    try:
                        if self._gl is not None:
                            self._gl.glDepthMask(False)
                            did_set_depth_mask = True
                    except Exception:
                        pass

                # model matrix (same as mesh path)
                if self._mgl_scale_multiplier != 1.0:
                    model = transform * Matrix44.from_scale([self._mgl_scale_multiplier] * 3, dtype="f4")
                else:
                    model = transform

                # write splat matrices (splatq shader uses Proj/View/Model)
                try:
                    self._mgl_splatq_prog["Proj"].write(np.asarray(proj, dtype="f4").tobytes())
                    self._mgl_splatq_prog["View"].write(np.asarray(lookat, dtype="f4").tobytes())
                    self._mgl_splatq_prog["Model"].write(np.asarray(model, dtype="f4").tobytes())
                except Exception:
                    pass
                try:
                    self._mgl_apply_shadow_uniforms(self._mgl_splatq_prog)
                    self._mgl_splatq_prog["UseSplatLighting"].value = (
                        1 if bool(getattr(self, "_mgl_splats_has_lit", False)) else 0
                    )
                except Exception:
                    pass

                try:
                    if np is not None:
                        self._mgl_pick_proj = np.array(proj, dtype="f4").T
                        self._mgl_pick_view = np.array(lookat, dtype="f4").T
                        self._mgl_pick_model = np.array(model, dtype="f4").T
                except Exception:
                    pass

                # do not multiply by _mgl_scale_multiplier here
                self._mgl_splatq_prog["SplatWorldScale"].value = float(self._mgl_splat_world_scale)
                # tick + gate sorting
                self._mgl_splat_sort_tick = (self._mgl_splat_sort_tick + 1) % 1000000
                force_sort = bool(getattr(self, "_mgl_splat_force_sort", False))
                sort_every_frame = bool(getattr(self, "_mgl_splat_sort_every_frame", False))
                do_sort = bool(force_sort or sort_every_frame or ((self._mgl_splat_sort_tick % 10) == 0))

                # SORT. Static splats use a cadence; animated skinned splats force this
                # on frames where their positions changed so transparency does not lag.
                did_sort = False
                try:
                    if do_sort and np is not None:
                        cpu = getattr(self, "_mgl_splats15_cpu", None)
                        lit_cpu = getattr(self, "_mgl_splat_lit_cpu", None)
                        glow_cpu = getattr(self, "_mgl_splat_glow_cpu", None)
                        if cpu is not None and self._mgl_splatq_vbo is not None and cpu.shape[0] > 1:

                            view_model = (lookat * model).astype("f4")

                            pos = cpu[:, 0:3].astype(np.float32, copy=False)
                            ones = np.ones((pos.shape[0], 1), dtype=np.float32)
                            pos4 = np.concatenate([pos, ones], axis=1)

                            viewp = pos4 @ view_model
                            z = viewp[:, 2].astype(np.float32, copy=False)

                            # back-to-front for OpenGL-style view where forward is -Z:
                            # far has more negative z, so ascending draws far -> near
                            order = np.argsort(z)

                            if dbg:
                                print(
                                    "[SPLATQ] sort(viewZ). zmin/zmax:",
                                    float(z.min()),
                                    float(z.max()),
                                    "count:",
                                    int(cpu.shape[0]),
                                    flush=True,
                                )

                            self._mgl_splatq_vbo.write(cpu[order].tobytes())
                            try:
                                lit_vbo = getattr(self, "_mgl_splatq_lit_vbo", None)
                                if lit_vbo is not None and lit_cpu is not None:
                                    lit_sorted = np.asarray(lit_cpu, dtype=np.float32).reshape(-1, 1)[order]
                                    lit_vbo.write(lit_sorted.tobytes())
                            except Exception:
                                pass
                            try:
                                glow_vbo = getattr(self, "_mgl_splatq_glow_vbo", None)
                                if glow_vbo is not None and glow_cpu is not None:
                                    glow_sorted = np.asarray(glow_cpu, dtype=np.float32).reshape(-1, 1)[order]
                                    glow_vbo.write(glow_sorted.tobytes())
                            except Exception:
                                pass
                            did_sort = True
                except Exception as exc:
                    if dbg:
                        print("[SPLATQ] sort error:", exc, flush=True)
                if force_sort and did_sort:
                    try:
                        self._mgl_splat_force_sort = False
                    except Exception:
                        pass

                # draw
                inst = int(self._mgl_splat_count)

                # culling can leak from mesh pass; disable it for splats
                had_cull = bool(getattr(self, "_mgl_cull_enabled", False))
                try:
                    self._mgl_ctx.disable(moderngl.CULL_FACE)
                except Exception:
                    pass

                try:
                    self._mgl_splatq_vao.render(
                        mode=moderngl.TRIANGLE_STRIP,
                        vertices=4,
                        instances=inst,
                    )
                finally:
                    # restore cull state
                    if had_cull:
                        try:
                            self._mgl_ctx.enable(moderngl.CULL_FACE)
                        except Exception:
                            pass

                    # restore depth mask
                    try:
                        if did_set_depth_mask:
                            try:
                                self._mgl_ctx.depth_mask = prev_depth_mask
                            except Exception:
                                if self._gl is not None:
                                    self._gl.glDepthMask(bool(prev_depth_mask))
                    except Exception:
                        pass

                    # restore depth test
                    try:
                        if prev_depth_test:
                            self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                        else:
                            self._mgl_ctx.disable(moderngl.DEPTH_TEST)
                    except Exception:
                        pass
                    try:
                        if prev_depth_func is not None:
                            self._mgl_ctx.depth_func = prev_depth_func
                    except Exception:
                        pass

        except Exception as exc:
            import traceback
            if dbg:
                print("[SPLATQ] PAINT CRASH:", exc)
            traceback.print_exc()

    def _paint_mgl_draw_splat_wireframe_pass(self, *, mvp) -> None:
        if (
            self._mgl_wireframe
            and self._mgl_render_splats
            and self._mgl_splat_bbox_vao is not None
        ):
            try:
                self._mgl_ctx.line_width = float(self._mgl_wire_line_width)
            except Exception:
                pass
            try:
                self._mgl_grid_prog["Mvp"].write(mvp.astype("f4"))
                self._mgl_grid_prog["Color"].value = self._mgl_wire_color
                try:
                    self._mgl_grid_prog["GridOffset"].value = (0.0, 0.0)
                    self._mgl_grid_prog["FadeOrigin"].value = (0.0, 0.0)
                    self._mgl_grid_prog["FadeStart"].value = 0.0
                    self._mgl_grid_prog["FadeEnd"].value = 0.0
                    self._mgl_grid_prog["MajorBoost"].value = 1.0
                    self._mgl_grid_prog["MajorStep"].value = 1.0
                    self._mgl_grid_prog["GridSpacing"].value = 1.0
                except Exception:
                    pass
            except Exception:
                pass
            self._mgl_splat_bbox_vao.render(moderngl.LINES)
            try:
                self._mgl_ctx.line_width = 1.0
            except Exception:
                pass

    def _paint_mgl_update_frame_timing(self) -> None:
        now = time.perf_counter()
        dt = now - getattr(self, "_fps_last_t", now)
        self._fps_last_t = now

        if dt > 0.0:
            if self._fps_ema <= 0.0:
                self._fps_ema = dt
            else:
                self._fps_ema = self._fps_ema * 0.9 + dt * 0.1
            self._fps = 1.0 / max(self._fps_ema, 1e-6)

        frame_id = int(getattr(self, "_mgl_frame_id", 0) or 0) + 1
        self._mgl_frame_id = frame_id
        try:
            if not math.isfinite(dt) or dt < 0.0:
                dt = 0.0
        except Exception:
            dt = 0.0
        # Clamp large gaps so procedural animations don't "jump" on first frame.
        step = min(max(float(dt), 0.0), 0.1)
        proc_time_override = None
        try:
            timeline_timing = getattr(self, "_timeline_material_timing", None)
            if callable(timeline_timing):
                resolved_step, resolved_time = timeline_timing(step)
                step = max(0.0, float(resolved_step))
                if resolved_time is not None:
                    proc_time_override = max(0.0, float(resolved_time))
        except Exception:
            proc_time_override = None
        try:
            self._mgl_update_procedural_textures(step, frame_id, proc_time_override=proc_time_override)
        except Exception:
            pass

    def _paint_mgl_prepare_framebuffer(self, *, dbg) -> bool:
        if not _HAS_MGL or self._mgl_ctx is None:
            try:
                c = self._viewport_bg
                self._gl.glClearColor(c.redF(), c.greenF(), c.blueF(), 1.0)
            except Exception:
                pass
            return False
        try:
            vp_w, vp_h = self._mgl_render_size()
            active_viewport = self._mgl_active_render_viewport()
            self._mgl_active_viewport_rect = active_viewport
            self._mgl_bind_default_fbo()
            # QOpenGLWidget already has the correct default framebuffer bound.
            # Avoid Framebuffer.clear() because it may bind/use() internally and can hard-crash some drivers.
            self._dbgprint(dbg, "[MGL] set viewport", flush=True)
            self._mgl_ctx.viewport = (0, 0, max(2, int(vp_w)), max(2, int(vp_h)))
            try:
                # Clear the whole render target first. The camera film gate reapplies scissor below.
                self._mgl_ctx.scissor = None
            except Exception:
                pass
            self._dbgprint(dbg, "[MGL] after viewport assign", flush=True)

            col = self._mgl_bg_color or (0.15, 0.15, 0.15, 1.0)
            if len(col) >= 4:
                r, g, b, a = col[:4]
            else:
                r, g, b = col[:3]
                a = 1.0

            try:
                if self._gl is not None:
                    w = max(2, int(vp_w))
                    h = max(2, int(vp_h))
                    self._dbgprint(dbg, "[MGL] before glViewport", flush=True)
                    self._gl.glViewport(0, 0, w, h)
                    try:
                        self._gl.glDisable(GL_SCISSOR_TEST)
                    except Exception:
                        pass
                    self._dbgprint(dbg, "[MGL] after glViewport", flush=True)

                    self._dbgprint(dbg, "[MGL] before glClearColor", flush=True)
                    self._gl.glClearColor(r, g, b, a)
                    self._dbgprint(dbg, "[MGL] after glClearColor", flush=True)

                    # bind active target FBO (default widget FBO or render override target)
                    try:
                        fbo = int(self._mgl_target_framebuffer_id())
                        self._gl.glBindFramebuffer(0x8D40, fbo)  # GL_FRAMEBUFFER
                    except Exception:
                        pass

                    self._dbgprint(dbg, "[MGL] before glClear", flush=True)
                    try:
                        self._gl.glClearDepth(1.0)
                    except Exception:
                        pass
                    self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
                    self._dbgprint(dbg, "[MGL] after glClear", flush=True)

            except Exception:
                # Fallback: raw GL clear
                try:
                    if self._gl is not None:
                        try:
                            fbo = int(self._mgl_target_framebuffer_id())
                            self._gl.glBindFramebuffer(0x8D40, fbo)  # GL_FRAMEBUFFER
                        except Exception:
                            pass

                        self._gl.glClearColor(r, g, b, a)
                        try:
                            self._gl.glDisable(GL_SCISSOR_TEST)
                        except Exception:
                            pass
                        try:
                            self._gl.glClearDepth(1.0)
                        except Exception:
                            pass
                        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
                except Exception:
                    pass

            self._dbgprint(dbg, "[MGL] after raw gl clear block", flush=True)

            # Ensure target framebuffer is bound (snapshot/grab can change FBO binding)
            try:
                if self._gl is not None and hasattr(self, "defaultFramebufferObject"):
                    fbo = int(self._mgl_target_framebuffer_id())
                    self._gl.glBindFramebuffer(0x8D40, fbo)  # GL_FRAMEBUFFER
            except Exception:
                pass
            try:
                self._mgl_ctx.viewport = tuple(int(v) for v in active_viewport)
            except Exception:
                pass
            try:
                if tuple(int(v) for v in active_viewport) == (0, 0, max(2, int(vp_w)), max(2, int(vp_h))):
                    self._mgl_ctx.scissor = None
                else:
                    self._mgl_ctx.scissor = tuple(int(v) for v in active_viewport)
            except Exception:
                pass
            try:
                if self._gl is not None:
                    ax, ay, aw, ah = (int(v) for v in active_viewport)
                    self._gl.glViewport(ax, ay, max(2, aw), max(2, ah))
            except Exception:
                pass

        except Exception as exc:
            import traceback
            self._mgl_error = f"ModernGL framebuffer error: {exc}"
            self._dbgprint(dbg, "[MGL] framebuffer exception:", exc, flush=True)
            traceback.print_exc()
            return False
        return True

    def _paint_mgl_setup_context_state(self, *, dbg) -> bool:
        flags = moderngl.BLEND | moderngl.DEPTH_TEST
        if self._mgl_cull_enabled:
            flags |= moderngl.CULL_FACE
        self._dbgprint(dbg, "[MGL] before mgl enable", flush=True)
        self._mgl_ctx.enable(flags)
        self._dbgprint(dbg, "[MGL] after mgl enable", flush=True)

        self._mgl_ctx.wireframe = False
        try:
            self._mgl_ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        except Exception:
            pass
        try:
            # Ensure depth writes are on before drawing the scene.
            self._mgl_ctx.depth_mask = True
        except Exception:
            pass

        self._dbgprint(dbg, "[MGL] after wireframe", flush=True)
        if self._mgl_prog is None or self._mgl_grid_prog is None:
            return False
        self._dbgprint(dbg, "[MGL] prog ok", flush=True)

        pending_grid = getattr(self, "_mgl_grid_model_pending_path", None)
        if pending_grid is not None:
            self._mgl_grid_model_pending_path = None

            # Only load the legacy grid-model if it is explicitly enabled.
            if bool(getattr(self, "_mgl_grid_model_visible", False)):
                self._mgl_load_grid_model(Path(pending_grid), in_paint=True)
        return True

    def _paint_mgl_build_matrices(self, *, dbg):
        active_viewport = getattr(self, "_mgl_active_viewport_rect", None)
        if isinstance(active_viewport, (list, tuple)) and len(active_viewport) >= 4:
            try:
                vp_w, vp_h = max(2, int(active_viewport[2])), max(2, int(active_viewport[3]))
            except Exception:
                vp_w, vp_h = self._mgl_render_size()
        else:
            vp_w, vp_h = self._mgl_render_size()
        aspect = float(vp_w) / max(1.0, float(vp_h))
        try:
            zoom = float(getattr(self, "_mgl_camera_zoom", 1.0))
        except Exception:
            zoom = 1.0
        try:
            far = float(getattr(self, "_mgl_clip_far", 1000.0))
        except Exception:
            far = 1000.0
        try:
            use_fps_cam = bool(getattr(self, "_fps_camera_active", False)) and getattr(self, "_fps_camera", None) is not None
        except Exception:
            use_fps_cam = False
        # Keep the near plane large enough for stable depth/shadow edges, but below close-camera distances.
        near = max(0.002, min(0.2, max(zoom * 0.02, far / 20000.0)))
        try:
            near = min(float(near), max(0.002, float(zoom) * 0.25))
        except Exception:
            pass
        if use_fps_cam and np is not None:
            try:
                cam = getattr(self, "_fps_camera", None)
                cam_pos = np.asarray(getattr(cam, "position", (0.0, 0.0, 0.0)), dtype=np.float32).reshape(-1)[:3]
                if cam_pos.shape[0] >= 3 and np.all(np.isfinite(cam_pos)):
                    fps_near = max(0.002, min(0.2, far / 20000.0))
                    bounds = self._mgl_shadow_scene_bounds()
                    if bounds is not None:
                        center, radius = bounds
                        center = np.asarray(center, dtype=np.float32).reshape(-1)[:3]
                        if center.shape[0] >= 3 and np.all(np.isfinite(center)):
                            dist = float(np.linalg.norm(cam_pos - center))
                            if math.isfinite(dist):
                                try:
                                    radius = max(0.0, float(radius))
                                except Exception:
                                    radius = 0.0
                                surface_dist = max(0.0, dist - radius)
                                if surface_dist > 0.0:
                                    fps_near = max(fps_near, min(0.2, surface_dist * 0.05))
                                fps_near = min(fps_near, max(0.002, max(dist, 0.05) * 0.25))
                    near = float(fps_near)
            except Exception:
                pass
        if far <= near:
            far = near + 1.0
        proj = Matrix44.perspective_projection(self._mgl_fov, aspect, near, far)
        self._dbgprint(dbg, "[MGL] proj ok", flush=True)

        self._dbgprint(dbg, "[MGL] before lookat", flush=True)
        lookat = Matrix44.look_at(
            (0.0, 0.0, float(self._mgl_camera_zoom)),
            (0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
        )
        self._dbgprint(dbg, "[MGL] after lookat", flush=True)
        self._dbgprint(dbg, "[MGL] before transform build", flush=True)

        if self._mgl_arcball is not None and self._mgl_center is not None:
            self._mgl_arcball.Transform[3, :3] = -self._mgl_arcball.Transform[:3, :3].T @ self._mgl_center

        # build transform safely
        if self._mgl_arcball is not None:
            try:
                src = self._mgl_arcball.Transform
                if hasattr(src, "tolist"):
                    transform = Matrix44(src.tolist(), dtype="f4")
                else:
                    transform = Matrix44(src, dtype="f4")
            except Exception:
                transform = Matrix44.identity(dtype="f4")
        else:
            transform = Matrix44.identity(dtype="f4")

        # If FPS camera is active, override view matrix and bypass arcball transform.
        if use_fps_cam:
            try:
                cam = getattr(self, "_fps_camera", None)
                roll_locked = bool(getattr(self, "_mgl_orbit_locked", True))
                if cam is not None:
                    lookat = cam.view_matrix(roll_locked=roll_locked)
                    transform = Matrix44.identity(dtype="f4")
            except Exception:
                pass
        # cache matrices for picking (screen click -> ray)
        try:
            if np is not None:
                self._mgl_pick_proj = np.array(proj, dtype="f4").T
                self._mgl_pick_view = np.array(lookat, dtype="f4").T
                self._mgl_pick_model = np.array(transform, dtype="f4").T
        except Exception:
            pass
        # cache camera world position for debug + grid falloff
        try:
            cam_world = None
            if np is not None:
                if use_fps_cam:
                    cam = getattr(self, "_fps_camera", None)
                    if cam is not None:
                        pos = np.array(getattr(cam, "position", (0.0, 0.0, 0.0)), dtype=np.float32)
                        cam_world = (float(pos[0]), float(pos[1]), float(pos[2]))
                if cam_world is None:
                    arc = getattr(self, "_mgl_arcball", None)
                    center = getattr(self, "_mgl_center", None)
                    zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
                    if arc is not None and hasattr(arc, "Transform"):
                        rot = np.array(arc.Transform[:3, :3], dtype=np.float32)
                        # remove uniform scale so we only apply rotation
                        scale = float(np.linalg.norm(rot, ord="fro") / math.sqrt(3.0))
                        if scale > 1e-6:
                            rot = rot / scale
                        cam_local = np.array([0.0, 0.0, zoom], dtype=np.float32)
                        cam_rot = rot.T @ cam_local
                        if center is not None:
                            cam_world = (
                                float(cam_rot[0] + float(center[0])),
                                float(cam_rot[1] + float(center[1])),
                                float(cam_rot[2] + float(center[2])),
                            )
                        else:
                            cam_world = (float(cam_rot[0]), float(cam_rot[1]), float(cam_rot[2]))
                if cam_world is None:
                    view_mat = (lookat * transform).astype("f4")
                    view_np = np.array(view_mat, dtype=np.float32)
                    inv = np.linalg.inv(view_np)
                    cam = inv @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                    cam_world = (float(cam[0]), float(cam[1]), float(cam[2]))
            self._mgl_cam_world = cam_world
        except Exception:
            self._mgl_cam_world = None

        # cache stable 3x3 rotation for gizmo (no yaw/pitch)
        try:
            self._gizmo_rot3 = (
                (float(transform[0][0]), float(transform[0][1]), float(transform[0][2])),
                (float(transform[1][0]), float(transform[1][1]), float(transform[1][2])),
                (float(transform[2][0]), float(transform[2][1]), float(transform[2][2])),
            )
        except Exception:
            self._gizmo_rot3 = None

        self._dbgprint(dbg, "[MGL] after transform build", flush=True)
        self._dbgprint(dbg, "[MGL] before mvp compute", flush=True)

        if self._mgl_scale_multiplier != 1.0:
            scale_mat = Matrix44.from_scale(
                [self._mgl_scale_multiplier] * 3,
                dtype="f4",
            )
            mvp = proj * lookat * transform * scale_mat
        else:
            mvp = proj * lookat * transform
        self._dbgprint(dbg, "[MGL] after mvp compute", flush=True)

        self._dbgprint(dbg, "[MGL] before Mvp write", flush=True)
        self._mgl_prog["Mvp"].write(mvp.astype("f4").tobytes())
        self._dbgprint(dbg, "[MGL] after Mvp write", flush=True)
        return proj, lookat, transform, mvp

    def _paint_mgl_apply_scene_visibility_and_upload(self, *, mvp) -> None:
        # Apply any pending visibility changes in GL context (safe).
        try:
            if bool(getattr(self, "_mgl_visibility_dirty", False)):
                self._mgl_visibility_dirty = False
                pending = getattr(self, "_mgl_pending_visibility", None)
                if isinstance(pending, dict):
                    for key, vis in list(pending.items()):
                        try:
                            self._mgl_set_scene_item_visibility(key, bool(vis))
                        except Exception:
                            pass
                    pending.clear()
                # Recompute splat render flag after visibility updates
                try:
                    splat_map = getattr(self, "_mgl_scene_splats", None) or {}
                    visibility = getattr(self, "_mgl_scene_visibility", {}) or {}
                    any_visible = False
                    if isinstance(splat_map, dict):
                        for k in splat_map.keys():
                            if bool(visibility.get(k, True)):
                                any_visible = True
                                break
                    self._mgl_render_splats = bool(any_visible)
                    need_rebuild = False
                    if self._mgl_render_splats:
                        need_rebuild = bool(
                            getattr(self, "_mgl_splats_need_rebuild", False)
                            or getattr(self, "_mgl_splatq_vao", None) is None
                            or not bool(getattr(self, "_mgl_splat_count", 0))
                        )
                    self._mgl_splats_visibility_dirty = bool(need_rebuild)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._mgl_retarget_refresh_dynamic_handles()
        except Exception:
            pass
        try:
            self._mgl_scene_skeleton_refresh_dynamic_handles()
        except Exception:
            pass

        try:
            if self._mgl_update_skinned_splat_proxies():
                if bool(getattr(self, "_mgl_render_splats", False)):
                    self._mgl_splats_visibility_dirty = False
                    self._mgl_splats_rebuild_ts = time.time()
                    try:
                        self._mgl_rebuild_scene_splats(preserve_camera=True)
                    except Exception as exc:
                        self._mgl_splats_visibility_dirty = True
                        try:
                            self._mgl_log("skinned_splat_proxy: animated rebuild failed err=" + repr(exc))
                        except Exception:
                            pass
                else:
                    self._mgl_splats_visibility_dirty = True
        except Exception:
            pass

        # If splat visibility changed, rebuild in GL context.
        try:
            if bool(getattr(self, "_mgl_splats_visibility_dirty", False)):
                if bool(getattr(self, "_mgl_render_splats", False)):
                    now = time.time()
                    last = float(getattr(self, "_mgl_splats_rebuild_ts", 0.0) or 0.0)
                    min_dt = float(getattr(self, "_mgl_splats_rebuild_min_dt", 0.05) or 0.05)
                    if now - last < min_dt:
                        # Keep dirty flag set; try again next frame.
                        self._mgl_splats_visibility_dirty = True
                    else:
                        self._mgl_splats_visibility_dirty = False
                        self._mgl_splats_rebuild_ts = now
                        try:
                            self._mgl_rebuild_scene_splats(preserve_camera=True)
                        except Exception as exc:
                            try:
                                self._mgl_log("splats: rebuild (visibility dirty) failed err=" + repr(exc))
                            except Exception:
                                pass
                else:
                    self._mgl_splats_visibility_dirty = False
        except Exception:
            pass

        self._mgl_upload_pending_splats()

        try:
            self._mgl_render_shadow_map()
        except Exception:
            try:
                self._mgl_shadow_valid = False
            except Exception:
                pass

        scene = getattr(self, "_mgl_scene", None)
        try:
            self._mgl_pending_transparent_scene_items = []
        except Exception:
            pass
        if scene is not None:
            try:
                items = [item for item in sorted(scene.items(), key=lambda it: it.order) if bool(getattr(item, "visible", False))]
            except Exception:
                items = []
            transparent_items = [item for item in items if self._mgl_scene_item_is_transparent(item)]
            if transparent_items:
                try:
                    self._mgl_pending_transparent_scene_items = list(transparent_items)
                except Exception:
                    self._mgl_pending_transparent_scene_items = []
                transparent_ids = {id(item) for item in transparent_items}
                opaque_items = [item for item in items if id(item) not in transparent_ids]
                for item in opaque_items:
                    item.draw(self, mvp)
            else:
                scene.draw(self, mvp)

    def _paint_mgl_draw_transparent_scene_pass(self, *, mvp) -> None:
        try:
            items = list(getattr(self, "_mgl_pending_transparent_scene_items", None) or [])
        except Exception:
            items = []
        try:
            self._mgl_material_scene_valid = False
        except Exception:
            pass
        if not items:
            return
        owners = []
        refract_owners = []
        for item in items:
            payload = getattr(item, "payload", None) or {}
            material = self._mgl_normalize_material(payload.get("material"))
            if material is None:
                continue
            owners.append(str(payload.get("owner") or item.name or ""))
            if self._mgl_material_uses_refraction(material):
                refract_owners.append(str(payload.get("owner") or item.name or ""))
        if owners:
            self._mgl_material_log_throttled(
                "transparent-pass",
                "renderer.transparent_pass",
                interval=1.0,
                count=len(owners),
                owners=owners[:12],
            )
        if refract_owners:
            try:
                self._mgl_capture_material_refraction_scene()
            except Exception:
                pass
            self._mgl_material_log_throttled(
                "transparent-pass-refraction",
                "renderer.transparent_pass_refraction",
                interval=1.0,
                count=len(refract_owners),
                owners=refract_owners[:12],
                captured=bool(getattr(self, "_mgl_material_scene_valid", False)),
            )
        prev_depth_mask = None
        prev_depth_func = None
        prev_wireframe = None
        prev_polygon_offset = None
        try:
            prev_depth_mask = getattr(self._mgl_ctx, "depth_mask", None)
        except Exception:
            prev_depth_mask = None
        try:
            prev_depth_func = getattr(self._mgl_ctx, "depth_func", None)
        except Exception:
            prev_depth_func = None
        try:
            prev_wireframe = bool(getattr(self._mgl_ctx, "wireframe", False))
        except Exception:
            prev_wireframe = None
        try:
            prev_polygon_offset = getattr(self._mgl_ctx, "polygon_offset", None)
        except Exception:
            prev_polygon_offset = None
        try:
            self._mgl_ctx.enable(moderngl.BLEND | moderngl.DEPTH_TEST)
        except Exception:
            pass
        try:
            self._mgl_ctx.disable(moderngl.CULL_FACE)
        except Exception:
            pass
        try:
            self._mgl_ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        except Exception:
            pass
        try:
            self._mgl_ctx.depth_func = "<="
        except Exception:
            pass
        try:
            self._mgl_ctx.wireframe = False
        except Exception:
            pass
        try:
            self._mgl_ctx.polygon_offset = (0.0, 0.0)
        except Exception:
            pass
        try:
            self._mgl_ctx.depth_mask = False
        except Exception:
            pass
        try:
            for item in items:
                item.draw(self, mvp)
        finally:
            try:
                self._mgl_pending_transparent_scene_items = []
            except Exception:
                pass
            if prev_depth_mask is not None:
                try:
                    self._mgl_ctx.depth_mask = prev_depth_mask
                except Exception:
                    pass
            if prev_depth_func is not None:
                try:
                    self._mgl_ctx.depth_func = prev_depth_func
                except Exception:
                    pass
            if prev_wireframe is not None:
                try:
                    self._mgl_ctx.wireframe = prev_wireframe
                except Exception:
                    pass
            if prev_polygon_offset is not None:
                try:
                    self._mgl_ctx.polygon_offset = prev_polygon_offset
                except Exception:
                    pass

    def _paint_mgl_draw_scene_skeleton_overlay_pass(self, *, mvp) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            return
        active_owner = str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "").strip().lower()
        if not active_owner:
            return
        items = []
        try:
            for item in sorted(scene.items(), key=lambda it: int(getattr(it, "order", 0) or 0)):
                if not bool(getattr(item, "visible", False)):
                    continue
                tag = str(getattr(item, "tag", "") or "")
                payload = getattr(item, "payload", None) or {}
                owner = str(payload.get("owner") or "").strip().lower()
                if owner != active_owner:
                    continue
                if tag == "scene-skeleton-handles" or (
                    tag == "scene-rig-joints" and bool(payload.get("scene_skeleton_overlay", False))
                ):
                    items.append(item)
        except Exception:
            items = []
        if not items:
            self._mgl_scene_skeleton_log_throttled(
                f"post_splat_overlay_empty:{active_owner}",
                "post_splat_overlay_empty",
                interval=1.0,
                owner=active_owner,
            )
            return

        prev_depth_mask = None
        prev_depth_func = None
        prev_depth_test = True
        prev_wireframe = None
        prev_polygon_offset = None
        try:
            prev_depth_test = bool(getattr(self._mgl_ctx, "depth_test", True))
        except Exception:
            prev_depth_test = True
        try:
            prev_depth_mask = getattr(self._mgl_ctx, "depth_mask", None)
        except Exception:
            prev_depth_mask = None
        try:
            prev_depth_func = getattr(self._mgl_ctx, "depth_func", None)
        except Exception:
            prev_depth_func = None
        try:
            prev_wireframe = bool(getattr(self._mgl_ctx, "wireframe", False))
        except Exception:
            prev_wireframe = None
        try:
            prev_polygon_offset = getattr(self._mgl_ctx, "polygon_offset", None)
        except Exception:
            prev_polygon_offset = None

        try:
            self._mgl_ctx.enable(moderngl.BLEND)
        except Exception:
            pass
        try:
            self._mgl_ctx.disable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        except Exception:
            pass
        try:
            self._mgl_ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        except Exception:
            pass
        try:
            self._mgl_ctx.depth_mask = False
        except Exception:
            pass
        try:
            self._mgl_ctx.wireframe = False
        except Exception:
            pass
        try:
            self._mgl_ctx.polygon_offset = (0.0, 0.0)
        except Exception:
            pass

        drawn = 0
        try:
            for item in items:
                item.draw(self, mvp)
                drawn += 1
            self._mgl_scene_skeleton_log_throttled(
                f"post_splat_overlay_draw:{active_owner}",
                "post_splat_overlay_draw",
                interval=1.0,
                owner=active_owner,
                item_count=int(drawn),
                render_splats=bool(getattr(self, "_mgl_render_splats", False)),
                splat_count=int(getattr(self, "_mgl_splat_count", 0) or 0),
            )
        except Exception as exc:
            self._mgl_scene_skeleton_log(
                "post_splat_overlay_error",
                owner=active_owner,
                error=repr(exc),
            )
        finally:
            if prev_depth_mask is not None:
                try:
                    self._mgl_ctx.depth_mask = prev_depth_mask
                except Exception:
                    pass
            if prev_depth_func is not None:
                try:
                    self._mgl_ctx.depth_func = prev_depth_func
                except Exception:
                    pass
            if prev_wireframe is not None:
                try:
                    self._mgl_ctx.wireframe = prev_wireframe
                except Exception:
                    pass
            if prev_polygon_offset is not None:
                try:
                    self._mgl_ctx.polygon_offset = prev_polygon_offset
                except Exception:
                    pass
            try:
                if prev_depth_test:
                    self._mgl_ctx.enable(moderngl.DEPTH_TEST)
                else:
                    self._mgl_ctx.disable(moderngl.DEPTH_TEST)
            except Exception:
                pass
            try:
                if bool(getattr(self, "_mgl_cull_enabled", False)):
                    self._mgl_ctx.enable(moderngl.CULL_FACE)
                else:
                    self._mgl_ctx.disable(moderngl.CULL_FACE)
            except Exception:
                pass

    def _paint_mgl(self) -> None:
        if getattr(self, "_render_paused", False):
            return
        self._paint_mgl_update_frame_timing()

        dbg = bool(getattr(self, "_mgl_debug", False))
        self._dbgprint(dbg, "[MGL] ENTER _paint_mgl", flush=True)

        if not self._paint_mgl_prepare_framebuffer(dbg=dbg):
            return
        if not self._paint_mgl_setup_context_state(dbg=dbg):
            return

        proj, lookat, transform, mvp = self._paint_mgl_build_matrices(dbg=dbg)
        self._paint_mgl_apply_scene_visibility_and_upload(mvp=mvp)

        self._paint_mgl_draw_splats_pass(
            proj=proj,
            lookat=lookat,
            transform=transform,
        )
        self._paint_mgl_draw_splat_wireframe_pass(mvp=mvp)
        self._paint_mgl_draw_grid_pass(mvp=mvp)
        self._paint_mgl_draw_transparent_scene_pass(mvp=mvp)
        self._paint_mgl_draw_scene_skeleton_overlay_pass(mvp=mvp)



    def _mgl_get_camera_state(self) -> dict:
        """Return a JSON-serializable camera/orbit state (ModernGL path)."""
        state: dict = {}
        try:
            state["zoom"] = float(getattr(self, "_mgl_camera_zoom", 0.0))
        except Exception:
            state["zoom"] = 0.0

        # center can be None
        c = getattr(self, "_mgl_center", None)
        if c is None:
            state["center"] = None
        else:
            try:
                state["center"] = [float(c[0]), float(c[1]), float(c[2])]
            except Exception:
                state["center"] = None

        # arcball transform (4x4)
        arc = getattr(self, "_mgl_arcball", None)
        if arc is not None and hasattr(arc, "Transform"):
            try:
                t = arc.Transform
                # numpy array or nested list
                if hasattr(t, "tolist"):
                    state["arcball_transform"] = t.tolist()
                else:
                    state["arcball_transform"] = [[float(x) for x in row] for row in t]
            except Exception:
                state["arcball_transform"] = None
        else:
            state["arcball_transform"] = None

        # optional extras
        try:
            state["fov"] = float(getattr(self, "_mgl_fov", 60.0))
        except Exception:
            state["fov"] = 60.0

        try:
            state["projection"] = str(getattr(self, "_mgl_projection", "perspective"))
        except Exception:
            state["projection"] = "perspective"

        try:
            state["clip_far"] = float(getattr(self, "_mgl_clip_far", 1000.0))
        except Exception:
            state["clip_far"] = 1000.0

        try:
            state["scale_multiplier"] = float(getattr(self, "_mgl_scale_multiplier", 1.0))
        except Exception:
            state["scale_multiplier"] = 1.0

        try:
            state["splat_scale"] = float(getattr(self, "_splat_scale", 1.0))
        except Exception:
            state["splat_scale"] = 1.0

        try:
            state["fps_camera_active"] = bool(getattr(self, "_fps_camera_active", False))
            cam = getattr(self, "_fps_camera", None)
            if cam is not None and np is not None:
                pos = np.array(getattr(cam, "position", (0.0, 0.0, 0.0)), dtype=np.float32).reshape(3)
                fwd = np.array(getattr(cam, "forward", (0.0, 0.0, -1.0)), dtype=np.float32).reshape(3)
                up = np.array(getattr(cam, "up", (0.0, 1.0, 0.0)), dtype=np.float32).reshape(3)
                state["fps_camera"] = {
                    "position": [float(pos[0]), float(pos[1]), float(pos[2])],
                    "forward": [float(fwd[0]), float(fwd[1]), float(fwd[2])],
                    "up": [float(up[0]), float(up[1]), float(up[2])],
                }
        except Exception:
            pass

        # scene xforms (mesh + splat) for snapshot persistence
        try:
            offset_keys = None
            try:
                offset_map = getattr(self, "_mgl_scene_xform_offset_by_owner", None)
                if isinstance(offset_map, dict) and offset_map:
                    offset_keys = {str(k).strip().lower() for k in offset_map.keys()}
            except Exception:
                offset_keys = None

            def _xf_is_identity(pos, rot, scl) -> bool:
                try:
                    return (
                        all(abs(float(v)) < 1e-6 for v in (pos or (0.0, 0.0, 0.0)))
                        and all(abs(float(v)) < 1e-6 for v in (rot or (0.0, 0.0, 0.0)))
                        and all(abs(float(v) - 1.0) < 1e-6 for v in (scl or (1.0, 1.0, 1.0)))
                    )
                except Exception:
                    return False

            mesh_xf = {}
            raw_mesh = getattr(self, "_mgl_scene_xforms_by_owner", None)
            if isinstance(raw_mesh, dict):
                for owner, xf in raw_mesh.items():
                    if not isinstance(xf, dict):
                        continue
                    try:
                        pos = [float(v) for v in xf.get("pos", (0.0, 0.0, 0.0))]
                        rot = [float(v) for v in xf.get("rot", (0.0, 0.0, 0.0))]
                        scl = [float(v) for v in xf.get("scl", (1.0, 1.0, 1.0))]
                    except Exception:
                        continue
                    try:
                        if offset_keys:
                            key = str(owner).strip().lower()
                            if key in offset_keys and _xf_is_identity(pos, rot, scl):
                                continue
                    except Exception:
                        pass
                    mesh_xf[str(owner)] = {"pos": pos, "rot": rot, "scl": scl}

            splat_xf = {}
            raw_splat = getattr(self, "_mgl_scene_splat_xforms_by_owner", None)
            if isinstance(raw_splat, dict):
                for owner, xf in raw_splat.items():
                    if not isinstance(xf, dict):
                        continue
                    try:
                        pos = [float(v) for v in xf.get("pos", (0.0, 0.0, 0.0))]
                        rot = [float(v) for v in xf.get("rot", (0.0, 0.0, 0.0))]
                        scl = [float(v) for v in xf.get("scl", (1.0, 1.0, 1.0))]
                    except Exception:
                        continue
                    splat_xf[str(owner)] = {"pos": pos, "rot": rot, "scl": scl}

            # Reclassify any splat owners that were stored in mesh_xf.
            try:
                splat_map = getattr(self, "_mgl_scene_splats", None) or {}
                if isinstance(splat_map, dict) and splat_map:
                    splat_keys = {str(k).strip().lower() for k in splat_map.keys()}
                    for owner in list(mesh_xf.keys()):
                        key = str(owner).strip().lower()
                        if key in splat_keys and owner not in splat_xf:
                            splat_xf[str(owner)] = mesh_xf[owner]
                            try:
                                del mesh_xf[owner]
                            except Exception:
                                pass
            except Exception:
                pass

            if mesh_xf or splat_xf:
                state["scene_xforms"] = {"mesh": mesh_xf, "splat": splat_xf}
        except Exception:
            pass
        return state

    def _mgl_queue_camera_state(self, state: dict) -> None:
        """Queue camera state to apply after splats finish uploading/framing."""
        if not isinstance(state, dict):
            return
        try:
            apply_scene_xforms = bool(state.get("_apply_scene_xforms", state.get("apply_scene_xforms", True)))
        except Exception:
            apply_scene_xforms = True

        if getattr(self, "_mgl_pending_splats", None) is None:
            self._mgl_pending_cam_state = None
            self._mgl_apply_camera_state(state)
            return

        pending_state = dict(state)
        if not bool(apply_scene_xforms):
            pending_state.pop("scene_xforms", None)
        self._mgl_pending_cam_state = pending_state

        dbg = bool(getattr(self, "_mgl_cam_debug", False))
        if dbg:
            print(
                "[CAMQ] queued keys:",
                sorted(list(state.keys())),
                "zoom:",
                state.get("zoom", None),
                "scale_multiplier:",
                state.get("scale_multiplier", None),
                "splat_scale:",
                state.get("splat_scale", None),
                flush=True,
            )

        # scene xforms (optional snapshot payload)
        try:
            xf_state = state.get("scene_xforms", None) if bool(apply_scene_xforms) else None
            if isinstance(xf_state, dict):
                mesh_xf = xf_state.get("mesh") or xf_state.get("meshes") or {}
                splat_xf = xf_state.get("splat") or xf_state.get("splats") or {}

                # Reclassify any splat owners that were stored under mesh_xf.
                try:
                    splat_map = getattr(self, "_mgl_scene_splats", None) or {}
                    if isinstance(splat_map, dict) and splat_map:
                        splat_keys = {str(k).strip().lower() for k in splat_map.keys()}
                        for owner in list(mesh_xf.keys()) if isinstance(mesh_xf, dict) else []:
                            key = str(owner).strip().lower()
                            if key in splat_keys and isinstance(mesh_xf.get(owner), dict):
                                splat_xf = dict(splat_xf) if not isinstance(splat_xf, dict) else splat_xf
                                splat_xf[str(owner)] = mesh_xf[owner]
                                try:
                                    del mesh_xf[owner]
                                except Exception:
                                    pass
                except Exception:
                    pass

                offset_keys = None
                try:
                    offset_map = getattr(self, "_mgl_scene_xform_offset_by_owner", None)
                    if isinstance(offset_map, dict) and offset_map:
                        offset_keys = {str(k).strip().lower() for k in offset_map.keys()}
                except Exception:
                    offset_keys = None

                def _xf_is_identity(pos, rot, scl) -> bool:
                    try:
                        return (
                            all(abs(float(v)) < 1e-6 for v in (pos or (0.0, 0.0, 0.0)))
                            and all(abs(float(v)) < 1e-6 for v in (rot or (0.0, 0.0, 0.0)))
                            and all(abs(float(v) - 1.0) < 1e-6 for v in (scl or (1.0, 1.0, 1.0)))
                        )
                    except Exception:
                        return False

                updated_owners = []

                # Apply mesh transforms
                if isinstance(mesh_xf, dict):
                    for owner, xf in mesh_xf.items():
                        if not isinstance(xf, dict):
                            continue
                        try:
                            pos = xf.get("pos")
                            rot = xf.get("rot")
                            scl = xf.get("scl")
                            try:
                                if offset_keys:
                                    key = str(owner).strip().lower()
                                    if key in offset_keys and _xf_is_identity(pos, rot, scl):
                                        continue
                            except Exception:
                                pass
                            # update cache
                            d = getattr(self, "_mgl_scene_xforms_by_owner", None)
                            if not isinstance(d, dict):
                                d = {}
                                setattr(self, "_mgl_scene_xforms_by_owner", d)
                            d[str(owner)] = {
                                "pos": tuple(float(v) for v in (pos or (0.0, 0.0, 0.0))),
                                "rot": tuple(float(v) for v in (rot or (0.0, 0.0, 0.0))),
                                "scl": tuple(float(v) for v in (scl or (1.0, 1.0, 1.0))),
                            }
                            # apply to scene items if present
                            self._mgl_set_scene_asset_xform(
                                str(owner),
                                pos=pos,
                                rot=rot,
                                scl=scl,
                                apply_to_scene_models=True,
                                use_splat_xform=False,
                            )
                            updated_owners.append(str(owner))
                        except Exception:
                            continue

                # Apply splat transforms
                if isinstance(splat_xf, dict):
                    for owner, xf in splat_xf.items():
                        if not isinstance(xf, dict):
                            continue
                        try:
                            pos = xf.get("pos")
                            rot = xf.get("rot")
                            scl = xf.get("scl")
                            d = getattr(self, "_mgl_scene_splat_xforms_by_owner", None)
                            if not isinstance(d, dict):
                                d = {}
                                setattr(self, "_mgl_scene_splat_xforms_by_owner", d)
                            d[str(owner)] = {
                                "pos": tuple(float(v) for v in (pos or (0.0, 0.0, 0.0))),
                                "rot": tuple(float(v) for v in (rot or (0.0, 0.0, 0.0))),
                                "scl": tuple(float(v) for v in (scl or (1.0, 1.0, 1.0))),
                            }
                            self._mgl_set_scene_asset_xform(
                                str(owner),
                                pos=pos,
                                rot=rot,
                                scl=scl,
                                apply_to_scene_models=False,
                                use_splat_xform=True,
                            )
                            updated_owners.append(str(owner))
                        except Exception:
                            continue
                # Sync outliner values to the applied snapshot transforms.
                try:
                    w = self.window()
                    if w is not None and hasattr(w, "update_scene_asset_xform"):
                        for owner in updated_owners:
                            w.update_scene_asset_xform(owner)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.update()
        except Exception:
            pass

    def _mgl_apply_camera_state(self, state: dict) -> None:
        """Apply a camera/orbit state produced by _mgl_get_camera_state()."""
        if not isinstance(state, dict):
            return

        # zoom
        try:
            self._mgl_camera_zoom = float(state.get("zoom", getattr(self, "_mgl_camera_zoom", 0.0)))
        except Exception:
            pass

        # center
        c = state.get("center", None)
        if c is None:
            self._mgl_center = None
        else:
            try:
                self._mgl_center = [float(c[0]), float(c[1]), float(c[2])]
            except Exception:
                pass

        # Ignore snapshot scale multiplier; keep default scale 1.0 for all scenes.
        try:
            self._mgl_scale_multiplier = 1.0
            if hasattr(self, "_example_scale_label") and self._example_scale_label is not None:
                self._example_scale_label.setText("Scale 1.00x")
            if hasattr(self, "_example_scale_slider") and self._example_scale_slider is not None:
                self._example_scale_slider.setValue(100)
        except Exception:
            pass

        # splat scale
        try:
            ss = state.get("splat_scale", None)
            if ss is not None:
                self._splat_scale = float(ss)
                # if you have a slider widget, keep it in sync
                if hasattr(self, "_splat_scale_slider") and self._splat_scale_slider is not None:
                    self._splat_scale_slider.setValue(int(self._splat_scale * 100.0))
        except Exception:
            pass

        # arcball transform
        arc = getattr(self, "_mgl_arcball", None)
        t = state.get("arcball_transform", None)
        if arc is not None and t is not None and np is not None:
            try:
                arr = np.array(t, dtype="f4")
                if arr.shape == (4, 4):
                    arc.Transform[...] = arr

                    # make sure arcball bounds match current widget size
                    try:
                        if hasattr(arc, "setBounds"):
                            arc.setBounds(self.width(), self.height())
                    except Exception:
                        pass

                    # sync orientation using a clean rotation matrix (no scale/shear)
                    try:
                        rmat = arr[:3, :3].astype("f4", copy=False)

                        # Orthonormalize R -> nearest proper rotation
                        umat, _, vmat = np.linalg.svd(rmat)
                        r_norm = (umat @ vmat).astype("f4", copy=False)

                        # Fix reflection if det < 0
                        if np.linalg.det(r_norm) < 0:
                            umat[:, -1] *= -1.0
                            r_norm = (umat @ vmat).astype("f4", copy=False)

                        if hasattr(arc, "LastRot") and arc.LastRot is not None:
                            arc.LastRot[...] = r_norm
                        # some arcballs also use ThisRot
                        if hasattr(arc, "ThisRot") and getattr(arc, "ThisRot") is not None:
                            arc.ThisRot[...] = r_norm

                        if hasattr(arc, "isDragging"):
                            arc.isDragging = False
                    except Exception:
                        pass

                    # do not touch arc.click (it's a method)
            except Exception:
                pass

        # optional extras
        try:
            self._mgl_fov = float(state.get("fov", getattr(self, "_mgl_fov", 60.0)))
        except Exception:
            pass

        try:
            self._mgl_projection = str(state.get("projection", getattr(self, "_mgl_projection", "perspective")))
        except Exception:
            pass

        try:
            clip_far = state.get("clip_far", None)
            if clip_far is not None:
                self._mgl_clip_far = float(clip_far)
                if hasattr(self, "_mgl_clip_input") and self._mgl_clip_input is not None:
                    self._mgl_clip_input.setText(str(int(self._mgl_clip_far)))
        except Exception:
            pass

        try:
            fps_state = state.get("fps_camera", None)
            if isinstance(fps_state, dict) and np is not None:
                from echograph.ui.fps_camera import FpsCamera

                cam = getattr(self, "_fps_camera", None)
                if cam is None:
                    cam = FpsCamera()
                pos = fps_state.get("position", None)
                fwd = fps_state.get("forward", None)
                up = fps_state.get("up", None)
                if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                    cam.position = np.array([float(pos[0]), float(pos[1]), float(pos[2])], dtype=np.float32)
                if isinstance(fwd, (list, tuple)) and len(fwd) >= 3:
                    cam.forward = np.array([float(fwd[0]), float(fwd[1]), float(fwd[2])], dtype=np.float32)
                if isinstance(up, (list, tuple)) and len(up) >= 3:
                    cam.up = np.array([float(up[0]), float(up[1]), float(up[2])], dtype=np.float32)
                if hasattr(cam, "_orthonormalize"):
                    cam._orthonormalize()
                self._fps_camera = cam
                self._fps_camera_active = bool(state.get("fps_camera_active", True))
                if bool(self._fps_camera_active):
                    self._orbit_cam_enabled = False
                else:
                    self._orbit_cam_enabled = True
            elif "fps_camera_active" in state:
                self._fps_camera_active = bool(state.get("fps_camera_active", False))
                if not bool(self._fps_camera_active):
                    self._orbit_cam_enabled = True
                else:
                    self._orbit_cam_enabled = False
            else:
                self._fps_camera_active = False
                self._orbit_cam_enabled = True
        except Exception:
            pass

        try:
            self.update()
        except Exception:
            pass

    def _mgl_arcball_sync_after_set_transform(self) -> None:
        arc = getattr(self, "_mgl_arcball", None)
        if arc is None or not hasattr(arc, "Transform") or np is None:
            return

        try:
            t = np.array(arc.Transform, dtype="f4", copy=False)
            rmat = t[:3, :3]

            # matrix -> quaternion (x,y,z,w)
            tr = float(rmat[0, 0] + rmat[1, 1] + rmat[2, 2])
            if tr > 0.0:
                s = (tr + 1.0) ** 0.5 * 2.0
                qw = 0.25 * s
                qx = (rmat[2, 1] - rmat[1, 2]) / s
                qy = (rmat[0, 2] - rmat[2, 0]) / s
                qz = (rmat[1, 0] - rmat[0, 1]) / s
            elif (rmat[0, 0] > rmat[1, 1]) and (rmat[0, 0] > rmat[2, 2]):
                s = (1.0 + rmat[0, 0] - rmat[1, 1] - rmat[2, 2]) ** 0.5 * 2.0
                qw = (rmat[2, 1] - rmat[1, 2]) / s
                qx = 0.25 * s
                qy = (rmat[0, 1] + rmat[1, 0]) / s
                qz = (rmat[0, 2] + rmat[2, 0]) / s
            elif rmat[1, 1] > rmat[2, 2]:
                s = (1.0 + rmat[1, 1] - rmat[0, 0] - rmat[2, 2]) ** 0.5 * 2.0
                qw = (rmat[0, 2] - rmat[2, 0]) / s
                qx = (rmat[0, 1] + rmat[1, 0]) / s
                qy = 0.25 * s
                qz = (rmat[1, 2] + rmat[2, 1]) / s
            else:
                s = (1.0 + rmat[2, 2] - rmat[0, 0] - rmat[1, 1]) ** 0.5 * 2.0
                qw = (rmat[1, 0] - rmat[0, 1]) / s
                qx = (rmat[0, 2] + rmat[2, 0]) / s
                qy = (rmat[1, 2] + rmat[2, 1]) / s
                qz = 0.25 * s

            q = np.array([qx, qy, qz, qw], dtype="f4")
            n = float(np.linalg.norm(q))
            if n > 1e-8:
                q /= n

            # Sync common arcball internal fields so first drag does not "jump"
            for name in ("_qnow", "qnow", "_q_now", "q_now"):
                if hasattr(arc, name):
                    try:
                        setattr(arc, name, q.copy())
                    except Exception:
                        pass

            for name in ("_qdown", "qdown", "_q_down", "q_down"):
                if hasattr(arc, name):
                    try:
                        setattr(arc, name, q.copy())
                    except Exception:
                        pass

            # Also clear typical drag state if present
            for name, val in (
                ("_dragging", False),
                ("dragging", False),
                ("_isDragging", False),
                ("isDragging", False),
            ):
                if hasattr(arc, name):
                    try:
                        setattr(arc, name, val)
                    except Exception:
                        pass

        except Exception:
            pass

    def _on_mgl_pick_texture(self) -> None:
        if not self._use_moderngl:
            return
        if not _HAS_MGL:
            self._mgl_error = "ModernGL dependencies unavailable"
            self.update()
            return
        base = None
        if self._mgl_mesh_path:
            try:
                base = Path(self._mgl_mesh_path).parent
            except Exception:
                base = None
        if base is None:
            base = Path(__file__).resolve().parents[1] / "3dmodels"
        start_dir = str(base) if base and base.exists() else str(Path.home())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Texture",
            start_dir,
            "Image files (*.png *.jpg *.jpeg *.bmp *.tga);;All Files (*.*)",
        )
        if not path:
            return
        self._apply_texture_path(path)
        self.update()

    def _on_mgl_scale_changed(self, value: int) -> None:
        if not self._use_moderngl:
            return
        try:
            scale = max(0.01, float(value) / 100.0)
        except Exception:
            scale = 1.0
        self._mgl_scale_multiplier = scale
        if getattr(self, "_example_scale_label", None) is not None:
            self._example_scale_label.setText(f"Scale {scale:.2f}x")
        self.update()

    def _on_mgl_light_changed(self, value: int) -> None:
        if not self._use_moderngl:
            return
        try:
            intensity = min(10.0, max(0.0, float(value) / 100.0))
        except Exception:
            intensity = 1.0
        self._apply_mgl_light_intensity(intensity, sync_ui=True, sync_scene=True)

    def _apply_mgl_light_intensity(self, intensity: float, *, sync_ui: bool = True, sync_scene: bool = True) -> None:
        if not self._use_moderngl:
            return
        try:
            intensity = min(10.0, max(0.0, float(intensity)))
        except Exception:
            intensity = 1.0
        self._mgl_light_intensity = intensity
        if sync_ui:
            label = getattr(self, "_mgl_light_label", None)
            if label is not None:
                label.setText(f"Light {intensity:.2f}x")
            slider = getattr(self, "_mgl_light_slider", None)
            if slider is not None:
                try:
                    slider.blockSignals(True)
                    slider.setValue(int(round(intensity * 100.0)))
                finally:
                    slider.blockSignals(False)
        if sync_scene:
            scene = getattr(self, "_scene", None)
            if scene is not None:
                try:
                    settings = getattr(scene, "_view_settings", None)
                    if not isinstance(settings, dict):
                        settings = {}
                    settings = dict(settings)
                    settings["light_intensity"] = float(intensity)
                    scene._view_settings = settings
                except Exception:
                    pass
        self.update()

    def _on_mgl_ambient_toggled(self, checked: bool) -> None:
        self._apply_mgl_ambient_settings(enabled=bool(checked), sync_ui=False, sync_scene=True)

    def _apply_mgl_ambient_settings(
        self,
        *,
        enabled: Optional[bool] = None,
        strength: Optional[float] = None,
        sync_ui: bool = True,
        sync_scene: bool = True,
    ) -> None:
        if not self._use_moderngl:
            return
        if enabled is not None:
            self._mgl_ambient_light_enabled = bool(enabled)
        if strength is not None:
            try:
                self._mgl_ambient_light_strength = max(0.0, min(1.0, float(strength)))
            except Exception:
                self._mgl_ambient_light_strength = 0.10
        ambient = float(self._mgl_effective_ambient_light())
        for prog_name in ("_mgl_prog", "_mgl_splatq_prog"):
            try:
                prog = getattr(self, prog_name, None)
                if prog is not None:
                    prog["AmbientLight"].value = ambient
            except Exception:
                pass
        if sync_ui:
            toggle = getattr(self, "_mgl_ambient_toggle", None)
            if toggle is not None:
                try:
                    toggle.blockSignals(True)
                    toggle.setChecked(bool(getattr(self, "_mgl_ambient_light_enabled", True)))
                finally:
                    try:
                        toggle.blockSignals(False)
                    except Exception:
                        pass
        try:
            win = self.window()
        except Exception:
            win = None
        if win is not None and win is not self:
            try:
                if hasattr(win, "_ambient_light_enabled"):
                    win._ambient_light_enabled = bool(getattr(self, "_mgl_ambient_light_enabled", True))
                if hasattr(win, "_ambient_light_strength"):
                    win._ambient_light_strength = float(getattr(self, "_mgl_ambient_light_strength", 0.10))
                toggle = getattr(win, "_ambient_light_toggle", None)
                if toggle is not None:
                    try:
                        toggle.blockSignals(True)
                        toggle.setChecked(bool(getattr(self, "_mgl_ambient_light_enabled", True)))
                    finally:
                        try:
                            toggle.blockSignals(False)
                        except Exception:
                            pass
            except Exception:
                pass
        if sync_scene:
            scene = getattr(self, "_scene", None)
            if scene is not None:
                try:
                    settings = getattr(scene, "_view_settings", None)
                    if not isinstance(settings, dict):
                        settings = {}
                    settings = dict(settings)
                    settings["ambient_light"] = bool(getattr(self, "_mgl_ambient_light_enabled", True))
                    settings["ambient_light_strength"] = float(
                        getattr(self, "_mgl_ambient_light_strength", 0.10)
                    )
                    scene._view_settings = settings
                except Exception:
                    pass
        self.update()

    def _on_mgl_two_sided_shadows_toggled(self, checked: bool) -> None:
        self._apply_mgl_shadow_settings(two_sided_shadows=bool(checked), sync_scene=True)

    def _apply_mgl_shadow_settings(
        self,
        *,
        enabled: Optional[bool] = None,
        quality: Optional[str] = None,
        self_shadows: Optional[bool] = None,
        two_sided_shadows: Optional[bool] = None,
        sync_scene: bool = True,
    ) -> None:
        if not self._use_moderngl:
            return
        if enabled is not None:
            self._mgl_shadows_enabled = bool(enabled)
        if quality is not None:
            self._mgl_shadow_quality = self._mgl_shadow_quality_value(quality)
        if self_shadows is not None:
            self._mgl_self_shadows_enabled = bool(self_shadows)
        if two_sided_shadows is not None:
            self._mgl_two_sided_shadows_enabled = bool(two_sided_shadows)
        toggle = getattr(self, "_mgl_two_sided_shadow_toggle", None)
        if toggle is not None:
            try:
                toggle.blockSignals(True)
                toggle.setChecked(bool(getattr(self, "_mgl_two_sided_shadows_enabled", True)))
            finally:
                try:
                    toggle.blockSignals(False)
                except Exception:
                    pass
        try:
            win = self.window()
        except Exception:
            win = None
        if win is not None and win is not self:
            try:
                if hasattr(win, "_shadow_quality"):
                    win._shadow_quality = self._mgl_shadow_quality_value(getattr(self, "_mgl_shadow_quality", "low"))
                if hasattr(win, "_cast_shadows_enabled"):
                    win._cast_shadows_enabled = bool(getattr(self, "_mgl_shadows_enabled", True))
                if hasattr(win, "_self_shadows_enabled"):
                    win._self_shadows_enabled = bool(getattr(self, "_mgl_self_shadows_enabled", True))
                if hasattr(win, "_two_sided_shadows_enabled"):
                    win._two_sided_shadows_enabled = bool(getattr(self, "_mgl_two_sided_shadows_enabled", True))
                toggle = getattr(win, "_two_sided_shadows_toggle", None)
                if toggle is not None:
                    try:
                        toggle.blockSignals(True)
                        toggle.setChecked(bool(getattr(self, "_mgl_two_sided_shadows_enabled", True)))
                    finally:
                        try:
                            toggle.blockSignals(False)
                        except Exception:
                            pass
            except Exception:
                pass
        try:
            self._mgl_shadow_map_size = int(self._mgl_shadow_map_size_value())
        except Exception:
            pass
        try:
            self._mgl_shadow_dirty = True
            self._mgl_shadow_valid = False
            self._mgl_shadow_signature = None
        except Exception:
            pass
        if sync_scene:
            scene = getattr(self, "_scene", None)
            if scene is not None:
                try:
                    settings = getattr(scene, "_view_settings", None)
                    if not isinstance(settings, dict):
                        settings = {}
                    settings = dict(settings)
                    settings["cast_shadows"] = bool(getattr(self, "_mgl_shadows_enabled", True))
                    settings["shadow_quality"] = self._mgl_shadow_quality_value(getattr(self, "_mgl_shadow_quality", "low"))
                    settings["self_shadows"] = bool(getattr(self, "_mgl_self_shadows_enabled", True))
                    settings["two_sided_shadows"] = bool(
                        getattr(self, "_mgl_two_sided_shadows_enabled", True)
                    )
                    scene._view_settings = settings
                except Exception:
                    pass
        self.update()

    def _apply_mgl_wire_color(self, color, *, sync_scene: bool = True) -> None:
        if not self._use_moderngl:
            return
        rgba = (0.25, 0.25, 0.25, 1.0)
        try:
            if isinstance(color, QtGui.QColor):
                if color.isValid():
                    rgba = (
                        float(color.redF()),
                        float(color.greenF()),
                        float(color.blueF()),
                        float(color.alphaF()),
                    )
            elif isinstance(color, (list, tuple)):
                vals = [float(v) for v in color[:4]]
                if len(vals) >= 3:
                    if len(vals) < 4:
                        vals.append(1.0)
                    rgba = tuple(min(1.0, max(0.0, float(v))) for v in vals[:4])
        except Exception:
            rgba = (0.25, 0.25, 0.25, 1.0)

        self._mgl_wire_color = rgba
        try:
            if self._mgl_wire_prog is not None:
                self._mgl_wire_prog["Color"].value = rgba
        except Exception:
            pass
        try:
            scene_items = getattr(self, "_mgl_scene", None)
            if scene_items is not None:
                for item in scene_items.items():
                    tag = str(getattr(item, "tag", "") or "")
                    if tag not in ("model-wire", "scene-wire", "scene-volume", "scene-camera", "scene-light"):
                        continue
                    payload = getattr(item, "payload", None)
                    if isinstance(payload, dict):
                        payload["color"] = rgba
        except Exception:
            pass
        if sync_scene:
            scene = getattr(self, "_scene", None)
            if scene is not None:
                try:
                    settings = getattr(scene, "_view_settings", None)
                    if not isinstance(settings, dict):
                        settings = {}
                    settings = dict(settings)
                    settings["wire_color"] = [float(c) for c in rgba]
                    scene._view_settings = settings
                except Exception:
                    pass
        self.update()

    def _on_mgl_clip_changed(self, value: int | None = None) -> None:
        if not self._use_moderngl:
            return
        if value is None:
            text = ""
            widget = getattr(self, "_mgl_clip_input", None)
            if widget is not None:
                try:
                    text = widget.text().strip()
                except Exception:
                    text = ""
            try:
                value = int(text) if text else int(getattr(self, "_mgl_clip_far", 1000.0))
            except Exception:
                value = int(getattr(self, "_mgl_clip_far", 1000.0))
        try:
            clip_far = max(10.0, float(value))
        except Exception:
            clip_far = 1000.0
        self._mgl_clip_far = clip_far
        if getattr(self, "_mgl_clip_input", None) is not None:
            try:
                self._mgl_clip_input.setText(str(int(clip_far)))
            except Exception:
                pass
        self.update()

    def _on_mgl_wireframe_toggled(self, checked: bool) -> None:
        if not self._use_moderngl:
            return
        self._mgl_wireframe = bool(checked)
        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            scene.set_visible_by_tag("model-wire", bool(checked))
            if checked:
                visibility_map = getattr(self, "_mgl_scene_visibility", {}) or {}
                for item in scene.iter_by_tag("scene-wire"):
                    payload = item.payload or {}
                    owner = payload.get("owner") or payload.get("node")
                    if owner and not visibility_map.get(owner, True):
                        item.visible = False
                    else:
                        item.visible = True
            else:
                scene.set_visible_by_tag("scene-wire", False)
        self.update()

    def _on_mgl_uv_toggled(self, checked: bool) -> None:
        if not self._use_moderngl:
            return
        self._mgl_uv_overlay_enabled = bool(checked)
        self._mgl_uv_cache = None
        self.update()

    def _on_mgl_grid_fx_toggled(self, checked: bool) -> None:
        if not self._use_moderngl:
            return
        self._mgl_grid_fx_enabled = bool(checked)
        self.update()

    @staticmethod
    def _mgl_camera_distance(fov: float) -> float:
        return 1.0 / max(1e-6, math.tan(math.radians(float(fov) / 2.0)))

    def _init_mgl_renderer(self) -> None:
        if not _HAS_MGL:
            self._mgl_error = "ModernGL dependencies unavailable"
            return
        try:
            self._mgl_ctx = moderngl.create_context()
            self._mgl_default_mesh_instance_buffers = None
            self._mgl_ctx.enable(moderngl.BLEND | moderngl.DEPTH_TEST)
            self._mgl_ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)

            # mesh shaders
            mesh_vertex = SHADERS["mesh_vertex"]
            mesh_fragment = SHADERS["mesh_fragment"]
            grid_vertex = SHADERS["grid_vertex"]
            grid_fragment = SHADERS["grid_fragment"]
            wire_vertex = SHADERS["wire_vertex"]
            wire_fragment = SHADERS["wire_fragment"]
            shadow_vertex = SHADERS["shadow_vertex"]
            shadow_fragment = SHADERS["shadow_fragment"]

            self._mgl_prog = self._mgl_ctx.program(vertex_shader=mesh_vertex, fragment_shader=mesh_fragment)
            self._mgl_grid_prog = self._mgl_ctx.program(vertex_shader=grid_vertex, fragment_shader=grid_fragment)
            self._mgl_wire_prog = self._mgl_ctx.program(vertex_shader=wire_vertex, fragment_shader=wire_fragment)
            self._mgl_shadow_prog = self._mgl_ctx.program(vertex_shader=shadow_vertex, fragment_shader=shadow_fragment)
            try:
                self._mgl_prog["Light"].value = (1.0, 1.0, 1.0)
            except Exception:
                pass
            self._mgl_prog["Color"].value = self._mgl_mesh_color
            try:
                self._mgl_prog["Texture"].value = 0
                self._mgl_prog["ShadowMap"].value = 7
                self._mgl_prog["ShadowIdMap"].value = 8
                self._mgl_prog["SceneColorTex"].value = 5
                self._mgl_prog["UseTexture"].value = 0
                self._mgl_prog["UseVertexColor"].value = 0
                self._mgl_prog["LightDir"].value = self._mgl_light_direction_tuple()
                self._mgl_prog["LightPos"].value = self._mgl_light_position_tuple()
                self._mgl_prog["LightType"].value = int(
                    self._mgl_light_type_index(getattr(self, "_mgl_scene_light_type", "directional"))
                )
                self._mgl_prog["LightRange"].value = float(self._mgl_light_range_value())
                spot_inner_cos, spot_outer_cos = self._mgl_spot_cone_cosines()
                self._mgl_prog["SpotCosInner"].value = float(spot_inner_cos)
                self._mgl_prog["SpotCosOuter"].value = float(spot_outer_cos)
                self._mgl_prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
                self._mgl_prog["AmbientLight"].value = float(self._mgl_effective_ambient_light())
                self._mgl_prog["UseLighting"].value = 1
                self._mgl_prog["UseInstancing"].value = 0
                self._mgl_prog["UseShadows"].value = 0
                self._mgl_prog["UseSelfShadows"].value = 1
                self._mgl_prog["ShadowBias"].value = float(self._mgl_effective_shadow_bias())
                self._mgl_prog["ShadowDarkness"].value = float(self._mgl_effective_shadow_darkness())
                self._mgl_prog["ShadowReceiverId"].value = 0.0
                size = float(getattr(self, "_mgl_shadow_map_size", 2048) or 2048)
                self._mgl_prog["ShadowMapSize"].value = (size, size)
                self._mgl_prog["UseMaterial"].value = 0
                self._mgl_prog["MaterialTransparency"].value = 0.0
                self._mgl_prog["MaterialIor"].value = 1.0
                self._mgl_prog["MaterialTint"].value = (1.0, 1.0, 1.0)
                self._mgl_prog["MaterialFresnelAmount"].value = 0.0
                self._mgl_prog["MaterialFresnelColor"].value = (1.0, 1.0, 1.0)
                self._mgl_prog["UseSceneRefraction"].value = 0
                self._mgl_prog["ScreenSize"].value = (1.0, 1.0)
                self._mgl_prog["CameraWorldPos"].value = (0.0, 0.0, 4.0)
                self._mgl_prog["UseProcedural"].value = 0
                self._mgl_prog["UseProceduralLayer"].value = 0
                self._mgl_prog["UseVolumeMask"].value = 0
                self._mgl_prog["ProceduralMode"].value = 0
                self._mgl_prog["ProcParams"].value = (1.0, 1.0, 1.0, 0.0)
                self._mgl_prog["ProcSeed"].value = 0.0
                self._mgl_prog["ProcAnimSpeed"].value = 1.0
                self._mgl_prog["ProcEmissive"].value = 0.0
                self._mgl_prog["ProcLightMix"].value = 1.0
                self._mgl_prog["ProcOffset"].value = (0.0, 0.0)
                self._mgl_prog["ProcPan"].value = 1.0
                self._mgl_prog["ProcLifeMin"].value = 3.0
                self._mgl_prog["ProcLifeMax"].value = 14.0
                self._mgl_prog["ProcChainMin"].value = 0.8
                self._mgl_prog["ProcChainMax"].value = 2.2
                self._mgl_prog["ProceduralMode2"].value = 0
                self._mgl_prog["ProcParams2"].value = (1.0, 1.0, 1.0, 0.0)
                self._mgl_prog["ProcSeed2"].value = 0.0
                self._mgl_prog["ProcAnimSpeed2"].value = 1.0
                self._mgl_prog["ProcEmissive2"].value = 0.0
                self._mgl_prog["ProcLightMix2"].value = 1.0
                self._mgl_prog["ProcOffset2"].value = (0.0, 0.0)
                self._mgl_prog["ProcPan2"].value = 1.0
                self._mgl_prog["ProcLifeMin2"].value = 3.0
                self._mgl_prog["ProcLifeMax2"].value = 14.0
                self._mgl_prog["ProcChainMin2"].value = 0.8
                self._mgl_prog["ProcChainMax2"].value = 2.2
                self._mgl_prog["ProcTime"].value = 0.0
                self._mgl_prog["ProcBgEnabled"].value = 0
                self._mgl_prog["ProcBg"].value = (0.0, 0.0, 0.0, 1.0)
                self._mgl_prog["ProcBgEnabled2"].value = 0
                self._mgl_prog["ProcBg2"].value = (0.0, 0.0, 0.0, 1.0)
                self._mgl_prog["ProcGlyph"].value = 1
                self._mgl_prog["ProcGlyphGrid"].value = (1.0, 1.0)
                self._mgl_prog["ProcGlyphCount"].value = 1.0
                if np is not None:
                    ident = np.eye(4, dtype="f4")
                    try:
                        self._mgl_prog["Model"].write(ident.tobytes())
                        self._mgl_prog["LightMvp"].write(ident.tobytes())
                        self._mgl_prog["VolumeInv"].write(ident.tobytes())
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                self._mgl_shadow_prog["UseInstancing"].value = 0
                self._mgl_shadow_prog["ShadowCasterId"].value = 0.0
            except Exception:
                pass
            self._mgl_grid_prog["Color"].value = (0.8, 0.8, 0.8, self._mgl_grid_alpha)
            try:
                self._mgl_grid_prog["ShadowMap"].value = 7
                self._mgl_grid_prog["UseShadows"].value = 0
                self._mgl_grid_prog["ShadowBias"].value = float(self._mgl_effective_shadow_bias())
                self._mgl_grid_prog["ShadowDarkness"].value = float(self._mgl_effective_shadow_darkness())
                size = float(getattr(self, "_mgl_shadow_map_size", 2048) or 2048)
                self._mgl_grid_prog["ShadowMapSize"].value = (size, size)
                if np is not None:
                    ident = np.eye(4, dtype="f4")
                    self._mgl_grid_prog["LightMvp"].write(ident.tobytes())
            except Exception:
                pass
            try:
                self._mgl_wire_prog["Color"].value = self._mgl_wire_color
                self._mgl_wire_prog["LineWidth"].value = float(
                    getattr(self, "_mgl_wire_edge_width", getattr(self, "_mgl_wire_line_width", 1.0))
                )
            except Exception:
                pass
            self._mgl_arcball = _ArcBallUtil(self.width(), self.height())
            self._mgl_center = np.zeros(3, dtype="f4")
            self._mgl_camera_zoom = self._mgl_camera_distance(self._mgl_fov)
            self._mgl_material_refraction_scale = 0.5
            self._mgl_update_grid()
            self._mgl_error = ""

            # splat shaders
            splat_vertex = SHADERS["splat_vertex"]
            splat_fragment = SHADERS["splat_fragment"]
            splatq_vertex = SHADERS["splatq_vertex"]
            splatq_fragment = SHADERS["splatq_fragment"]
            splat_shadow_vertex = SHADERS["splat_shadow_vertex"]
            splat_shadow_fragment = SHADERS["splat_shadow_fragment"]

            self._mgl_splat_prog = self._mgl_ctx.program(vertex_shader=splat_vertex, fragment_shader=splat_fragment)
            self._mgl_splatq_prog = self._mgl_ctx.program(vertex_shader=splatq_vertex, fragment_shader=splatq_fragment)
            self._mgl_splat_shadow_prog = self._mgl_ctx.program(
                vertex_shader=splat_shadow_vertex,
                fragment_shader=splat_shadow_fragment,
            )
            try:
                self._mgl_splatq_prog["ShadowMap"].value = 7
                self._mgl_splatq_prog["LightDir"].value = self._mgl_light_direction_tuple()
                self._mgl_splatq_prog["LightPos"].value = self._mgl_light_position_tuple()
                self._mgl_splatq_prog["LightType"].value = int(
                    self._mgl_light_type_index(getattr(self, "_mgl_scene_light_type", "directional"))
                )
                self._mgl_splatq_prog["LightRange"].value = float(self._mgl_light_range_value())
                spot_inner_cos, spot_outer_cos = self._mgl_spot_cone_cosines()
                self._mgl_splatq_prog["SpotCosInner"].value = float(spot_inner_cos)
                self._mgl_splatq_prog["SpotCosOuter"].value = float(spot_outer_cos)
                self._mgl_splatq_prog["LightIntensity"].value = float(self._mgl_effective_light_intensity())
                self._mgl_splatq_prog["AmbientLight"].value = float(self._mgl_effective_ambient_light())
                self._mgl_splatq_prog["UseSplatLighting"].value = 0
                self._mgl_splatq_prog["UseShadows"].value = 0
                self._mgl_splatq_prog["ShadowBias"].value = float(self._mgl_effective_shadow_bias())
                self._mgl_splatq_prog["ShadowDarkness"].value = float(self._mgl_effective_shadow_darkness())
                size = float(getattr(self, "_mgl_shadow_map_size", 2048) or 2048)
                self._mgl_splatq_prog["ShadowMapSize"].value = (size, size)
                if np is not None:
                    ident = np.eye(4, dtype="f4")
                    self._mgl_splatq_prog["LightMvp"].write(ident.tobytes())
            except Exception:
                pass

            # Static quad corners (TRIANGLE_STRIP, 4 verts)
            quad = np.array(
                [
                    -1.0,
                    -1.0,
                    1.0,
                    -1.0,
                    -1.0,
                    1.0,
                    1.0,
                    1.0,
                ],
                dtype="f4",
            )
            self._mgl_splatq_quad_vbo = self._mgl_ctx.buffer(quad.tobytes())

        except Exception as exc:
            self._mgl_error = str(exc)

    def set_splats(self, splats_np, *, lit_flags=None, glow_flags=None) -> None:
        """Queue splat instance data for GL-thread upload.
        Accepts (N,8), (N,10), (N,14), or (N,15) float32 arrays.

        (N,8):  [x,y,z, r,g,b,a, radius]
        (N,10): [x,y,z, r,g,b,a, radius, sx, sy]
        (N,14): [x,y,z, r,g,b,a, radius, sx, sy, qx, qy, qz, qw]
        (N,15): [x,y,z, r,g,b,a, radius, sx, sy, sz, qx, qy, qz, qw]
        lit_flags: optional per-splat lighting mask; omitted flags default to unlit.
        glow_flags: optional per-splat glow amount; omitted flags default to no glow.
        """
        if np is None:
            self._mgl_pending_splats = None
            self._mgl_pending_splat_lit_flags = None
            self._mgl_pending_splat_glow_flags = None
            self._mgl_render_splats = False
            self.update()
            return

        # IMPORTANT: do NOT clear scene models here.
        # Scenes can contain both meshes + splats, and set_splats() is used by scene loading too.

        arr = np.asarray(splats_np, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] not in (8, 10, 14, 15):
            raise ValueError(
                f"Expected splats_np shape (N,8) or (N,10) or (N,14) or (N,15), got {arr.shape}"
            )
        if lit_flags is None:
            lit_arr = np.zeros((int(arr.shape[0]), 1), dtype=np.float32)
        else:
            lit_arr = np.asarray(lit_flags, dtype=np.float32).reshape(-1, 1)
            if int(lit_arr.shape[0]) != int(arr.shape[0]):
                raise ValueError(
                    f"Expected lit_flags length {int(arr.shape[0])}, got {int(lit_arr.shape[0])}"
                )
            lit_arr = np.where(lit_arr > 0.5, np.float32(1.0), np.float32(0.0)).astype(np.float32, copy=False)
        if glow_flags is None:
            glow_arr = np.zeros((int(arr.shape[0]), 1), dtype=np.float32)
        else:
            glow_arr = np.asarray(glow_flags, dtype=np.float32).reshape(-1, 1)
            if int(glow_arr.shape[0]) != int(arr.shape[0]):
                raise ValueError(
                    f"Expected glow_flags length {int(arr.shape[0])}, got {int(glow_arr.shape[0])}"
                )
            glow_arr = np.clip(glow_arr, np.float32(0.0), np.float32(4.0)).astype(np.float32, copy=False)

        dbg = bool(getattr(self, "_mgl_debug", False))
        if dbg:
            print("[SPLAT] set_splats queue:", arr.shape, arr.dtype, flush=True)
        try:
            if bool(getattr(self, "_mgl_splat_log_verbose", False)):
                self._mgl_log_throttled(
                    "_mgl_splat_log_queue_ts",
                    "splats: set_splats queue shape=" + str(arr.shape),
                    1.0,
                )
        except Exception:
            pass

        self._mgl_pending_splats = arr
        self._mgl_pending_splat_lit_flags = lit_arr
        self._mgl_pending_splat_glow_flags = glow_arr
        self._mgl_render_splats = True
        self.update()

        if dbg:
            print("[SPLAT] set_splats update() called", flush=True)


    def _mgl_upload_pending_splats(self) -> None:
        dbg = bool(getattr(self, "_mgl_debug", False))
        self._dbgprint(dbg, "[MGL] ENTER _mgl_upload_pending_splats", flush=True)
        if self._mgl_pending_splats is None:
            return
        if not _HAS_MGL or self._mgl_ctx is None or self._mgl_splat_prog is None:
            return

        splats_np = self._mgl_pending_splats
        splat_lit_flags = getattr(self, "_mgl_pending_splat_lit_flags", None)
        splat_glow_flags = getattr(self, "_mgl_pending_splat_glow_flags", None)
        self._mgl_pending_splats = None
        self._mgl_pending_splat_lit_flags = None
        self._mgl_pending_splat_glow_flags = None
        try:
            if bool(getattr(self, "_mgl_splat_log_verbose", False)):
                self._mgl_log_throttled(
                    "_mgl_splat_log_upload_pending_ts",
                    "splats: upload pending shape=" + str(getattr(splats_np, "shape", None)),
                    1.0,
                )
        except Exception:
            pass

        # --- frame camera from bounds ---
        pos = splats_np[:, :3]
        mins = pos.min(axis=0)
        maxs = pos.max(axis=0)
        center = (mins + maxs) * 0.5
        extent = (maxs - mins) * 0.5
        radius = float(extent.max())

        self._mgl_center = center.astype("f4")
        try:
            self._mgl_base_center = self._mgl_center.copy()
        except Exception:
            self._mgl_base_center = self._mgl_center
        self._mgl_base_zoom = max(0.1, radius * 3.0)
        # simple "fit" distance
        self._mgl_camera_zoom = self._mgl_base_zoom

        # build/update splat bounds wireframe (box)
        if self._mgl_ctx is not None and self._mgl_grid_prog is not None:
            try:
                verts = np.array(debug_cube_wire_vertices(), dtype="f4").reshape(-1, 3)
                size = (maxs - mins).astype("f4")
                ctr = ((mins + maxs) * 0.5).astype("f4")
                verts = verts * size + ctr

                if self._mgl_splat_bbox_vao is not None:
                    try:
                        self._mgl_splat_bbox_vao.release()
                    except Exception:
                        pass
                if self._mgl_splat_bbox_vbo is not None:
                    try:
                        self._mgl_splat_bbox_vbo.release()
                    except Exception:
                        pass
                self._mgl_splat_bbox_vbo = self._mgl_ctx.buffer(verts.tobytes())
                self._mgl_splat_bbox_vao = self._mgl_ctx.simple_vertex_array(
                    self._mgl_grid_prog,
                    self._mgl_splat_bbox_vbo,
                    "in_position",
                )
            except Exception:
                self._mgl_splat_bbox_vao = None
                self._mgl_splat_bbox_vbo = None

        # update arcball size
        if self._mgl_arcball is not None:
            try:
                self._mgl_arcball.setBounds(self.width(), self.height())
            except Exception:
                pass

        # apply any queued camera state AFTER framing, BEFORE creating buffers/vaos
        pending = getattr(self, "_mgl_pending_cam_state", None)
        if isinstance(pending, dict):
            self._mgl_pending_cam_state = None

            dbg = bool(getattr(self, "_mgl_cam_debug", False))
            if dbg:
                print(
                    "[CAMAP] BEFORE apply",
                    "pending zoom:", pending.get("zoom", None),
                    "pending scale_multiplier:", pending.get("scale_multiplier", None),
                    flush=True,
                )

            self._mgl_apply_camera_state(pending)

            if dbg:
                print(
                    "[CAMAP] AFTER apply",
                    "zoom now:", getattr(self, "_mgl_camera_zoom", None),
                    "scale now:", getattr(self, "_mgl_scale_multiplier", None),
                    flush=True,
                )

        # update splat count
        self._mgl_splat_count = int(splats_np.shape[0])

        # upload per-vertex for splats (legacy path)
        if self._mgl_splat_vao is not None:
            try:
                self._mgl_splat_vao.release()
            except Exception:
                pass
            self._mgl_splat_vao = None

        if self._mgl_splat_vbo is not None:
            try:
                self._mgl_splat_vbo.release()
            except Exception:
                pass
            self._mgl_splat_vbo = None

        # upload per-instance for splat quads
        if self._mgl_splatq_vao is not None:
            try:
                self._mgl_splatq_vao.release()
            except Exception:
                pass
            self._mgl_splatq_vao = None

        if getattr(self, "_mgl_splat_shadow_vao", None) is not None:
            try:
                self._mgl_splat_shadow_vao.release()
            except Exception:
                pass
            self._mgl_splat_shadow_vao = None

        if self._mgl_splatq_vbo is not None:
            try:
                self._mgl_splatq_vbo.release()
            except Exception:
                pass
            self._mgl_splatq_vbo = None
        if getattr(self, "_mgl_splatq_lit_vbo", None) is not None:
            try:
                self._mgl_splatq_lit_vbo.release()
            except Exception:
                pass
            self._mgl_splatq_lit_vbo = None
        if getattr(self, "_mgl_splatq_glow_vbo", None) is not None:
            try:
                self._mgl_splatq_glow_vbo.release()
            except Exception:
                pass
            self._mgl_splatq_glow_vbo = None

        dbg = bool(getattr(self, "_mgl_debug", False))
        if dbg:
            print(
                "[SPLAT] framed center:",
                self._mgl_center,
                "radius:",
                radius,
                "zoom:",
                self._mgl_camera_zoom,
            )
            print(
                "[SPLAT] uploading:",
                self._mgl_splat_count,
                "ctx:",
                self._mgl_ctx is not None,
                "shape:",
                splats_np.shape,
            )

        self._mgl_splat_vbo = None
        self._mgl_splat_vao = None

        # if splatq shaders are ready, build the instanced path (preferred)
        if self._mgl_splatq_prog is not None and self._mgl_splatq_quad_vbo is not None:
            if splats_np.shape[1] == 8:
                # build default scale+quat for 8 -> 15
                ones = np.ones((splats_np.shape[0], 1), dtype=np.float32)
                zeros = np.zeros((splats_np.shape[0], 3), dtype=np.float32)
                scale = np.ones((splats_np.shape[0], 3), dtype=np.float32)
                splats15 = np.concatenate(
                    [splats_np[:, 0:8], scale, zeros, ones],
                    axis=1,
                )
            elif splats_np.shape[1] == 10:
                ones = np.ones((splats_np.shape[0], 1), dtype=np.float32)
                zeros = np.zeros((splats_np.shape[0], 3), dtype=np.float32)
                splats15 = np.concatenate(
                    [splats_np[:, 0:8], splats_np[:, 8:10], ones, zeros, ones],
                    axis=1,
                )
            elif splats_np.shape[1] == 14:
                ones = np.ones((splats_np.shape[0], 1), dtype=np.float32)
                splats15 = np.concatenate([splats_np[:, 0:10], ones, splats_np[:, 10:14]], axis=1)
            else:
                splats15 = splats_np

            if splats15.shape[1] == 15:
                self._mgl_splat_count = int(splats15.shape[0])
                try:
                    lit_cpu = np.asarray(splat_lit_flags, dtype=np.float32).reshape(-1, 1)
                    if int(lit_cpu.shape[0]) != int(splats15.shape[0]):
                        lit_cpu = np.zeros((int(splats15.shape[0]), 1), dtype=np.float32)
                except Exception:
                    lit_cpu = np.zeros((int(splats15.shape[0]), 1), dtype=np.float32)
                lit_cpu = np.where(lit_cpu > 0.5, np.float32(1.0), np.float32(0.0)).astype(np.float32, copy=False)
                try:
                    glow_cpu = np.asarray(splat_glow_flags, dtype=np.float32).reshape(-1, 1)
                    if int(glow_cpu.shape[0]) != int(splats15.shape[0]):
                        glow_cpu = np.zeros((int(splats15.shape[0]), 1), dtype=np.float32)
                except Exception:
                    glow_cpu = np.zeros((int(splats15.shape[0]), 1), dtype=np.float32)
                glow_cpu = np.clip(glow_cpu, np.float32(0.0), np.float32(4.0)).astype(np.float32, copy=False)

                # Keep CPU copy so we can sort per-frame (10k is fine)
                self._mgl_splats15_cpu = splats15
                self._mgl_splat_lit_cpu = lit_cpu
                self._mgl_splat_glow_cpu = glow_cpu
                try:
                    has_lit = bool(np.any(lit_cpu > 0.5))
                    self._mgl_splats_has_lit = has_lit
                    self._mgl_splats_all_lit = has_lit and bool(np.all(lit_cpu > 0.5))
                except Exception:
                    self._mgl_splats_has_lit = False
                    self._mgl_splats_all_lit = False

                # release previous GPU objects before replacing them
                try:
                    if self._mgl_splatq_vao is not None and hasattr(self._mgl_splatq_vao, "release"):
                        self._mgl_splatq_vao.release()
                except Exception:
                    pass
                try:
                    if getattr(self, "_mgl_splat_shadow_vao", None) is not None and hasattr(self._mgl_splat_shadow_vao, "release"):
                        self._mgl_splat_shadow_vao.release()
                except Exception:
                    pass
                try:
                    if self._mgl_splatq_vbo is not None and hasattr(self._mgl_splatq_vbo, "release"):
                        self._mgl_splatq_vbo.release()
                except Exception:
                    pass
                try:
                    if getattr(self, "_mgl_splatq_lit_vbo", None) is not None and hasattr(self._mgl_splatq_lit_vbo, "release"):
                        self._mgl_splatq_lit_vbo.release()
                except Exception:
                    pass
                try:
                    if getattr(self, "_mgl_splatq_glow_vbo", None) is not None and hasattr(self._mgl_splatq_glow_vbo, "release"):
                        self._mgl_splatq_glow_vbo.release()
                except Exception:
                    pass
                self._mgl_splatq_vao = None
                self._mgl_splat_shadow_vao = None
                self._mgl_splatq_vbo = None
                self._mgl_splatq_lit_vbo = None
                self._mgl_splatq_glow_vbo = None

                self._mgl_splatq_vbo = self._mgl_ctx.buffer(splats15.tobytes())
                self._mgl_splatq_lit_vbo = self._mgl_ctx.buffer(lit_cpu.tobytes())
                self._mgl_splatq_glow_vbo = self._mgl_ctx.buffer(glow_cpu.tobytes())
                self._mgl_splatq_vao = self._mgl_ctx.vertex_array(
                    self._mgl_splatq_prog,
                    [
                        (self._mgl_splatq_quad_vbo, "2f", "in_corner"),
                        (
                            self._mgl_splatq_vbo,
                            "3f 4f 1f 3f 4f /i",
                            "in_pos",
                            "in_col",
                            "in_rad",
                            "in_scale3",
                            "in_rot",
                        ),
                        (self._mgl_splatq_lit_vbo, "1f /i", "in_lit"),
                        (self._mgl_splatq_glow_vbo, "1f /i", "in_glow"),
                    ],
                )
                if getattr(self, "_mgl_splat_shadow_prog", None) is not None:
                    try:
                        self._mgl_splat_shadow_vao = self._mgl_ctx.vertex_array(
                            self._mgl_splat_shadow_prog,
                            [
                                (self._mgl_splatq_quad_vbo, "2f", "in_corner"),
                                (
                                    self._mgl_splatq_vbo,
                                    "3f 4f 1f 3f 4f /i",
                                    "in_pos",
                                    "in_col",
                                    "in_rad",
                                    "in_scale3",
                                    "in_rot",
                                ),
                            ],
                        )
                    except Exception:
                        self._mgl_splat_shadow_vao = None
                try:
                    self._mgl_shadow_dirty = True
                except Exception:
                    pass
                try:
                    if bool(getattr(self, "_mgl_splat_log_verbose", False)):
                        self._mgl_log_throttled(
                            "_mgl_splat_log_upload_done_ts",
                            "splats: upload done count=" + str(self._mgl_splat_count),
                            1.0,
                        )
                except Exception:
                    pass

    def _mgl_update_grid(self) -> None:
        if not _HAS_MGL or self._mgl_ctx is None:
            return
        try:
            self._mgl_log(
                "grid: update start size="
                + str(getattr(self, "_mgl_grid_size", None))
                + " cells="
                + str(getattr(self, "_mgl_grid_cells", None))
            )
        except Exception:
            pass
        try:
            base_size = float(getattr(self, "_mgl_grid_size", 20.0))
        except Exception:
            base_size = 20.0
        try:
            base_cells = int(getattr(self, "_mgl_grid_cells", 50))
        except Exception:
            base_cells = 50
        try:
            extend = float(getattr(self, "_mgl_grid_extend", 1.0))
        except Exception:
            extend = 1.0
        if not math.isfinite(extend) or extend < 1.0:
            extend = 1.0
        render_size = base_size * extend
        render_steps = max(3, int(round(base_cells * extend)))
        try:
            retarget_grid_size = float(self._mgl_retarget_grid_min_render_size())
        except Exception:
            retarget_grid_size = 0.0
        if retarget_grid_size > render_size:
            render_size = retarget_grid_size
        if (render_steps % 2) == 0:
            render_steps += 1
        try:
            self._mgl_grid_render_size = float(render_size)
            self._mgl_grid_render_steps = int(render_steps)
            denom = max(1, render_steps - 1)
            self._mgl_grid_spacing = float((render_size * 2.0) / float(denom))
        except Exception:
            pass

        grid = _mgl_grid(render_size, render_steps)
        grid = grid.astype("f4").reshape(-1, 3)
        self._mgl_grid_vertex_count = int(grid.shape[0])
        self._mgl_grid_vbo = self._mgl_ctx.buffer(grid.tobytes())
        if self._mgl_grid_prog is not None:
            self._mgl_grid_vao = self._mgl_ctx.simple_vertex_array(self._mgl_grid_prog, self._mgl_grid_vbo, "in_position")
        try:
            self._mgl_log(
                "grid: update done count="
                + str(self._mgl_grid_vertex_count)
                + " vao="
                + str(self._mgl_grid_vao is not None)
                + " vbo="
                + str(self._mgl_grid_vbo is not None)
            )
        except Exception:
            pass

    def _mgl_set_mesh(self, mesh) -> None:
        if not _HAS_MGL or self._mgl_ctx is None or mesh is None:
            return
        mesh.update_normals()
        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            scene.remove_by_tag("model")
            self._mgl_submeshes = []
        else:
            self._mgl_clear_submeshes()
        points = np.array(mesh.points(), dtype="f4")
        normals = np.array(mesh.vertex_normals(), dtype="f4")
        indices = np.array(mesh.face_vertex_indices(), dtype="u4").ravel()
        entry = self._mgl_build_mesh_entry(points, normals, None, indices=indices)
        if entry is None:
            return
        self._mgl_vao = entry.get("vao")
        self._mgl_mesh_vbos = [entry.get("vbo"), entry.get("nbo"), entry.get("tbo"), entry.get("cbo")]
        self._mgl_index_buffer = entry.get("ibo")
        self._mgl_mesh_vertex_count = int(entry.get("count", 0))
        self._mgl_mesh = mesh
        self._mgl_set_uv_overlay(None)
        self._mgl_init_arcball(points)
        if scene is not None:
            resources = [
                entry.get("vao"),
                entry.get("vbo"),
                entry.get("nbo"),
                entry.get("tbo"),
                entry.get("cbo"),
                entry.get("ibo"),
            ]
            item = MGLSceneItem(
                name="mesh",
                draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                payload={
                    "vao": entry.get("vao"),
                    "mesh_entry": entry,
                    "texture": None,
                    "color": self._mgl_mesh_color,
                },
                resources=[res for res in resources if res is not None],
                order=10,
                tag="model",
            )
            scene.add(item)

    def _mgl_clear_submeshes(self) -> None:
        if not self._mgl_submeshes:
            return
        for item in self._mgl_submeshes:
            tex = item.get("texture")
            if tex is not None:
                try:
                    tex.release()
                except Exception:
                    pass
            for key in ("vbo", "nbo", "tbo", "cbo", "ibo"):
                buf = item.get(key)
                if buf is not None:
                    try:
                        buf.release()
                    except Exception:
                        pass
            vao = item.get("vao")
            if vao is not None:
                try:
                    vao.release()
                except Exception:
                    pass
        self._mgl_submeshes = []

    def _mgl_set_raw_mesh(
        self,
        points: NDArray,
        normals: NDArray,
        uvs: Optional[NDArray] = None,
    ) -> None:
        if not _HAS_MGL or self._mgl_ctx is None:
            return
        if points.size == 0:
            return
        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            scene.remove_by_tag("model")
            self._mgl_submeshes = []
        else:
            self._mgl_clear_submeshes()
        entry = self._mgl_build_mesh_entry(points, normals, uvs)
        if entry is None:
            return
        self._mgl_vao = entry.get("vao")
        self._mgl_mesh_vbos = [entry.get("vbo"), entry.get("nbo"), entry.get("tbo"), entry.get("cbo")]
        self._mgl_index_buffer = entry.get("ibo")
        self._mgl_mesh_vertex_count = int(entry.get("count", 0))
        self._mgl_mesh = None
        self._mgl_set_uv_overlay(entry.get("uvs"))
        self._mgl_init_arcball(points)
        if scene is not None:
            resources = [
                entry.get("vao"),
                entry.get("vbo"),
                entry.get("nbo"),
                entry.get("tbo"),
                entry.get("cbo"),
                entry.get("ibo"),
            ]
            path_key = str(getattr(self, "_mgl_mesh_path", "") or "")
            is_fbx = path_key.strip().lower().endswith(".fbx")
            fbx_rig_context = None
            if is_fbx and path_key:
                try:
                    fbx_rig_context = self._mgl_fbx_rig_context_for_path(Path(path_key))
                except Exception:
                    fbx_rig_context = None
            item = MGLSceneItem(
                name="mesh",
                draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                payload={
                    "vao": entry.get("vao"),
                    "mesh_entry": entry,
                    "texture": None,
                    "color": self._mgl_mesh_color,
                    "path": path_key,
                    "edge_wire": bool(is_fbx),
                    "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                },
                resources=[res for res in resources if res is not None],
                order=10,
                tag="model",
            )
            scene.add(item)

    def _mgl_set_submeshes(self, submeshes: List[SubMeshData]) -> None:
        if not _HAS_MGL or self._mgl_ctx is None:
            return
        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            scene.remove_by_tag("model")
            self._mgl_submeshes = []
        else:
            self._mgl_clear_submeshes()
        if not submeshes:
            return
        entries, combined_uvs, texture_paths, total_indices = self._mgl_build_submesh_entries(submeshes)
        self._mgl_submeshes = entries
        self._mgl_mesh_vertex_count = total_indices
        self._mgl_mesh = None
        if combined_uvs:
            self._mgl_set_uv_overlay(np.concatenate(combined_uvs, axis=0))
        combined_points: List[NDArray] = []
        for sub in submeshes:
            combined_points.append(sub.points.astype("f4").reshape(-1, 3))
        if combined_points:
            self._mgl_init_arcball(np.concatenate(combined_points, axis=0))
        self._mgl_texture_paths = texture_paths
        if scene is not None:
            resources: List[object] = []
            for sub in entries:
                resources.extend(
                    [
                        sub.get("vao"),
                        sub.get("vbo"),
                        sub.get("nbo"),
                        sub.get("tbo"),
                        sub.get("cbo"),
                        sub.get("ibo"),
                        sub.get("texture"),
                    ]
                )
            path_key = str(getattr(self, "_mgl_mesh_path", "") or "")
            is_fbx = path_key.strip().lower().endswith(".fbx")
            fbx_rig_context = None
            if is_fbx and path_key:
                try:
                    fbx_rig_context = self._mgl_fbx_rig_context_for_path(Path(path_key))
                except Exception:
                    fbx_rig_context = None
            item = MGLSceneItem(
                name="mesh",
                draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                payload={
                    "submeshes": entries,
                    "path": path_key,
                    "edge_wire": bool(is_fbx),
                    "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                },
                resources=[res for res in resources if res is not None],
                order=10,
                tag="model",
            )
            scene.add(item)

    def _mgl_set_uv_overlay(self, uvs: Optional[NDArray]) -> None:
        if uvs is None or uvs.size == 0 or np is None:
            self._mgl_uv_segments = []
            self._mgl_uv_bounds = None
            self._mgl_uv_vertex_count = 0
            self._mgl_uv_cache = None
            return
        uvs = uvs.astype("f4").reshape(-1, 2)
        self._mgl_uv_vertex_count = int(uvs.shape[0])
        u_min = float(np.min(uvs[:, 0]))
        u_max = float(np.max(uvs[:, 0]))
        v_min = float(np.min(uvs[:, 1]))
        v_max = float(np.max(uvs[:, 1]))
        self._mgl_uv_bounds = (u_min, u_max, v_min, v_max)
        segments: List[Tuple[float, float, float, float]] = []
        for i in range(0, uvs.shape[0] - 2, 3):
            u0, v0 = uvs[i]
            u1, v1 = uvs[i + 1]
            u2, v2 = uvs[i + 2]
            segments.append((float(u0), float(v0), float(u1), float(v1)))
            segments.append((float(u1), float(v1), float(u2), float(v2)))
            segments.append((float(u2), float(v2), float(u0), float(v0)))
        self._mgl_uv_segments = segments
        self._mgl_uv_cache = None

    def _mgl_init_arcball(self, points: NDArray) -> None:
        if self._mgl_arcball is None:
            self._mgl_arcball = _ArcBallUtil(self.width(), self.height())

        bbox_min = np.min(points, axis=0)
        bbox_max = np.max(points, axis=0)
        self._mgl_center = 0.5 * (bbox_max + bbox_min)
        self._mgl_scale = float(np.linalg.norm(bbox_max - self._mgl_center))
        scale = max(self._mgl_scale, 1e-6)

        self._mgl_arcball.Transform = np.identity(4, "f4")
        self._mgl_arcball.Transform[:3, :3] /= scale
        self._mgl_arcball.Transform[3, :3] = -self._mgl_center / scale
        try:
            self._mgl_base_center = self._mgl_center.copy()
        except Exception:
            self._mgl_base_center = self._mgl_center
        self._mgl_base_zoom = self._mgl_camera_distance(self._mgl_fov)

        # keep internal arcball rotation state in sync with the forced Transform
        try:
            self._mgl_arcball.resetRotation()
        except Exception:
            pass

        self._mgl_camera_zoom = self._mgl_camera_distance(self._mgl_fov) * max(0.01, self._mgl_scale_multiplier)

    def _mgl_frame_camera(self) -> None:
        if bool(getattr(self, "_mgl_render_splats", False)) and np is not None:
            cpu = getattr(self, "_mgl_splats15_cpu", None)
            if cpu is not None and getattr(cpu, "size", 0) > 0:
                pos = cpu[:, :3]
                mins = pos.min(axis=0)
                maxs = pos.max(axis=0)
                center = (mins + maxs) * 0.5
                extent = (maxs - mins) * 0.5
                radius = float(extent.max())
                self._mgl_center = center.astype("f4")
                try:
                    self._mgl_base_center = self._mgl_center.copy()
                except Exception:
                    self._mgl_base_center = self._mgl_center
                base_zoom = max(0.1, radius * 3.0)
                self._mgl_base_zoom = base_zoom
                self._mgl_camera_zoom = float(base_zoom) * max(0.01, self._mgl_scale_multiplier)
                return

        base_center = getattr(self, "_mgl_base_center", None)
        if base_center is not None:
            try:
                self._mgl_center = base_center.copy()
            except Exception:
                self._mgl_center = base_center
        base_zoom = getattr(self, "_mgl_base_zoom", None)
        if base_zoom is None:
            base_zoom = self._mgl_camera_distance(self._mgl_fov)
        self._mgl_camera_zoom = float(base_zoom) * max(0.01, self._mgl_scale_multiplier)

    def _sync_mgl_gizmo(self, transform) -> None:
        # ultra-safe: do nothing unless we can read 3x3 floats safely
        try:
            r00 = float(transform[0][0]); r01 = float(transform[0][1]); r02 = float(transform[0][2])
            r10 = float(transform[1][0]); r11 = float(transform[1][1]); r12 = float(transform[1][2])
            r20 = float(transform[2][0]); r21 = float(transform[2][1]); r22 = float(transform[2][2])
        except Exception:
            return

        # forward = rot * (0,0,1) -> third column
        dx, dy, dz = r02, r12, r22
        dist2 = dx*dx + dy*dy + dz*dz
        if dist2 <= 1e-12:
            return
        dist = math.sqrt(dist2)

        self._cam_yaw = math.atan2(dx, dz)
        pitch = dy / dist
        if pitch < -1.0: pitch = -1.0
        if pitch >  1.0: pitch =  1.0
        self._cam_pitch = math.asin(pitch)

    def _mgl_load_mesh(self, path: Path) -> None:
        if self._mgl_ctx is None:
            self._mgl_error = "ModernGL context not ready"
            return
        self._mgl_fbx_joints_log_enabled = False
        try:
            self._mgl_fbx_joints_log(
                "================================ load_mesh ================================= "
                + f"path={path}"
            )
        except Exception:
            pass
        if self._mgl_texture is not None:
            try:
                self._mgl_texture.release()
            except Exception:
                pass
        self._mgl_texture = None
        self._mgl_texture_path = ""
        self._mgl_texture_paths = []
        self._mgl_texture_override = False
        if path.suffix.lower() == ".bvh":
            try:
                fbx_rig_context = self._mgl_fbx_rig_context_for_path(path)
            except Exception as exc:
                self._mgl_error = f"BVH load failed: {exc}"
                return
            if not isinstance(fbx_rig_context, dict) or fbx_rig_context.get("skeleton") is None:
                self._mgl_error = "BVH load failed: skeleton unavailable"
                return
            try:
                self.makeCurrent()
                self._mgl_error = ""
                scene = getattr(self, "_mgl_scene", None)
                if scene is not None:
                    self._mgl_clear_scene_models()
                    self._mgl_submeshes = []
                    self._mgl_vao = None
                    self._mgl_mesh_vbos = []
                    self._mgl_index_buffer = None

                    capture_debug_enabled, animated_debug_enabled = self._mgl_fbx_joint_debug_modes(
                        fbx_rig_context
                    )
                    if not capture_debug_enabled and not animated_debug_enabled:
                        animated_debug_enabled = True
                    joint_overlays: List[MGLSceneItem] = []
                    if capture_debug_enabled:
                        capture_item = self._mgl_add_fbx_joint_overlay_item(
                            path,
                            True,
                            pose_mode="capture",
                            owner=None,
                            path_key=str(path),
                            rig_context=fbx_rig_context,
                        )
                        if capture_item is not None:
                            capture_item.order = 16
                            joint_overlays.append(capture_item)
                    if animated_debug_enabled:
                        animated_item = self._mgl_add_fbx_joint_overlay_item(
                            path,
                            True,
                            pose_mode="animated",
                            owner=None,
                            path_key=str(path),
                            rig_context=fbx_rig_context,
                        )
                        if animated_item is not None:
                            animated_item.order = 16
                            joint_overlays.append(animated_item)

                    bounds_min = None
                    bounds_max = None
                    for overlay_item in joint_overlays:
                        scene.add(overlay_item)
                        try:
                            payload = dict(getattr(overlay_item, "payload", None) or {})
                            bmin = np.asarray(payload.get("bounds_min"), dtype="f4").reshape(-1)
                            bmax = np.asarray(payload.get("bounds_max"), dtype="f4").reshape(-1)
                            if bmin.size >= 3 and bmax.size >= 3:
                                b0 = bmin[:3].astype("f4", copy=False)
                                b1 = bmax[:3].astype("f4", copy=False)
                                if bounds_min is None or bounds_max is None:
                                    bounds_min, bounds_max = b0.copy(), b1.copy()
                                else:
                                    bounds_min = np.minimum(bounds_min, b0)
                                    bounds_max = np.maximum(bounds_max, b1)
                        except Exception:
                            pass
                    if bounds_min is not None and bounds_max is not None:
                        self._mgl_init_arcball(np.array([bounds_min, bounds_max], dtype="f4"))
                self._mgl_mesh_path = str(path)
                self._mgl_mesh_vertex_count = 0
                return
            except Exception as exc:
                self._mgl_error = f"BVH load failed: {exc}"
                return
        mesh = None
        if openmesh is not None and path.suffix.lower() != ".fbx":
            try:
                mesh = openmesh.read_trimesh(str(path))
            except Exception:
                mesh = None
        points = None
        normals = None
        uvs = None
        mesh_arrays = None
        fbx_rig_context = None
        if mesh is None:
            if path.suffix.lower() == ".fbx":
                try:
                    fbx_rig_context = self._mgl_fbx_rig_context_for_path(path)
                except Exception:
                    fbx_rig_context = None
                if isinstance(fbx_rig_context, dict):
                    self._mgl_fbx_joints_log_enabled = bool(fbx_rig_context.get("fbx_debug_log", False))
                try:
                    mesh_arrays = load_fbx_mesh_arrays_pyassimp(path)
                    points = mesh_arrays.points
                    normals = mesh_arrays.normals
                    uvs = mesh_arrays.uvs
                except Exception as exc:
                    mesh_arrays = self._mgl_fbx_mesh_arrays_from_context(fbx_rig_context)
                    if mesh_arrays is None:
                        self._mgl_error = f"FBX load failed: {exc}"
                        return
                    points = mesh_arrays.points
                    normals = mesh_arrays.normals
                    uvs = mesh_arrays.uvs
            elif path.suffix.lower() == ".obj":
                try:
                    points, normals, uvs = load_obj_mesh_arrays(path)
                except Exception:
                    points = None
            elif path.suffix.lower() in (".gltf", ".glb"):
                try:
                    mesh_arrays = load_gltf_mesh_arrays(path)
                    points = mesh_arrays.points
                    normals = mesh_arrays.normals
                    uvs = mesh_arrays.uvs
                except Exception:
                    points = None
            if points is None:
                model_data = load_model(path)
                if model_data is None or not model_data.vertices:
                    self._mgl_error = "Mesh load failed"
                    return
                points = np.array(model_data.vertices, dtype="f4").reshape(-1, 3)
                normals = np.zeros_like(points)
                for i in range(0, points.shape[0], 3):
                    a, b, c = points[i:i + 3]
                    n = np.cross(b - a, c - a)
                    norm = np.linalg.norm(n)
                    if norm > 1e-6:
                        n = n / norm
                    normals[i:i + 3] = n
        try:
            self.makeCurrent()
            self._mgl_error = ""
            scene = getattr(self, "_mgl_scene", None)
            if scene is not None:
                self._mgl_clear_scene_models()
                self._mgl_submeshes = []
                self._mgl_vao = None
                self._mgl_mesh_vbos = []
                self._mgl_index_buffer = None
            model_item = None
            fbx_bind_joints_only = bool(
                path.suffix.lower() == ".fbx"
                and self._mgl_fbx_bind_joints_only_enabled(
                    fbx_rig_context if isinstance(fbx_rig_context, dict) else None
                )
            )
            if path.suffix.lower() == ".fbx":
                context = fbx_rig_context if isinstance(fbx_rig_context, dict) else {}
                self._mgl_fbx_joints_log(
                    "load_mesh begin "
                    + f"path={path} has_context={bool(isinstance(fbx_rig_context, dict))} "
                    + f"has_skeleton={bool(context.get('skeleton') is not None)} "
                    + f"capture={bool(context.get('show_capture_joints', False))} "
                    + f"animated={bool(context.get('show_animated_joints', False))} "
                    + f"bind_only={bool(fbx_bind_joints_only)} wireframe={bool(getattr(self, '_mgl_wireframe', False))}"
                )
            if mesh is not None:
                mesh.update_normals()
                points = np.array(mesh.points(), dtype="f4")
                normals = np.array(mesh.vertex_normals(), dtype="f4")
                indices = np.array(mesh.face_vertex_indices(), dtype="u4").ravel()
                entry = self._mgl_build_mesh_entry(points, normals, None, indices=indices)
                if entry is None:
                    self._mgl_error = "Mesh upload failed"
                    return
                self._mgl_vao = entry.get("vao")
                self._mgl_mesh_vbos = [entry.get("vbo"), entry.get("nbo"), entry.get("tbo"), entry.get("cbo")]
                self._mgl_index_buffer = entry.get("ibo")
                resources = [
                    entry.get("vao"),
                    entry.get("vbo"),
                    entry.get("nbo"),
                    entry.get("tbo"),
                    entry.get("cbo"),
                    entry.get("ibo"),
                ]
                item = MGLSceneItem(
                    name=path.name,
                    draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                    payload={
                        "vao": entry.get("vao"),
                        "mesh_entry": entry,
                        "texture": None,
                        "color": self._mgl_mesh_color,
                        "path": str(path),
                        "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                        "fbx_bind_joints_only": bool(fbx_bind_joints_only),
                    },
                    resources=[res for res in resources if res is not None],
                    visible=not bool(fbx_bind_joints_only),
                    order=10,
                    tag="model",
                )
                if scene is not None:
                    scene.add(item)
                model_item = item
                self._mgl_mesh_vertex_count = int(entry.get("count", 0))
                self._mgl_mesh = mesh
                self._mgl_set_uv_overlay(None)
                self._mgl_init_arcball(points)
            else:
                if mesh_arrays is not None and mesh_arrays.submeshes:
                    if mesh_arrays.base_color is not None:
                        self._mgl_mesh_color = mesh_arrays.base_color
                    entries, combined_uvs, texture_paths, total_indices = self._mgl_build_submesh_entries(
                        mesh_arrays.submeshes
                    )
                    self._mgl_submeshes = entries
                    resources: List[object] = []
                    for sub in entries:
                        resources.extend(
                            [
                                sub.get("vao"),
                                sub.get("vbo"),
                                sub.get("nbo"),
                                sub.get("tbo"),
                                sub.get("cbo"),
                                sub.get("ibo"),
                                sub.get("texture"),
                            ]
                        )
                    item = MGLSceneItem(
                        name=path.name,
                        draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                        payload={
                            "submeshes": entries,
                            "path": str(path),
                            "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                            "fbx_bind_joints_only": bool(fbx_bind_joints_only),
                        },
                        resources=[res for res in resources if res is not None],
                        visible=not bool(fbx_bind_joints_only),
                        order=10,
                        tag="model",
                    )
                    if scene is not None:
                        scene.add(item)
                    model_item = item
                    self._mgl_mesh_vertex_count = int(total_indices)
                    if combined_uvs:
                        self._mgl_set_uv_overlay(np.concatenate(combined_uvs, axis=0))
                    else:
                        self._mgl_set_uv_overlay(None)
                    if texture_paths:
                        self._mgl_texture_paths = texture_paths
                    if mesh_arrays.points is not None and mesh_arrays.points.size:
                        self._mgl_init_arcball(mesh_arrays.points)
                else:
                    if mesh_arrays is not None and mesh_arrays.base_color is not None:
                        self._mgl_mesh_color = mesh_arrays.base_color
                    entry = self._mgl_build_mesh_entry(points, normals, uvs)
                    if entry is None:
                        self._mgl_error = "Mesh upload failed"
                        return
                    self._mgl_vao = entry.get("vao")
                    self._mgl_mesh_vbos = [entry.get("vbo"), entry.get("nbo"), entry.get("tbo"), entry.get("cbo")]
                    self._mgl_index_buffer = entry.get("ibo")
                    resources = [
                        entry.get("vao"),
                        entry.get("vbo"),
                        entry.get("nbo"),
                        entry.get("tbo"),
                        entry.get("cbo"),
                        entry.get("ibo"),
                    ]
                    item = MGLSceneItem(
                        name=path.name,
                        draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                        payload={
                            "vao": entry.get("vao"),
                            "mesh_entry": entry,
                            "texture": None,
                            "color": self._mgl_mesh_color,
                            "path": str(path),
                            "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                            "fbx_bind_joints_only": bool(fbx_bind_joints_only),
                        },
                        resources=[res for res in resources if res is not None],
                        visible=not bool(fbx_bind_joints_only),
                        order=10,
                        tag="model",
                    )
                    if scene is not None:
                        scene.add(item)
                    model_item = item
                    self._mgl_mesh_vertex_count = int(entry.get("count", 0))
                    self._mgl_set_uv_overlay(entry.get("uvs"))
                    self._mgl_init_arcball(points)
                    if mesh_arrays is not None and mesh_arrays.texture_path is not None:
                        try:
                            if not self._mgl_upload_texture_path(mesh_arrays.texture_path):
                                self._mgl_error = "Texture load failed"
                        except Exception as exc:
                            self._mgl_error = f"Texture upload failed: {exc}"
                    elif mesh_arrays is not None and mesh_arrays.texture_image is not None:
                        qimg = self._mgl_qimage_from_texture(mesh_arrays.texture_image)
                        if qimg is not None:
                            try:
                                self._mgl_upload_texture(qimg, str(path))
                            except Exception as exc:
                                self._mgl_error = f"Texture upload failed: {exc}"
            if scene is not None:
                ext = path.suffix.lower()
                wire_item = None
                if ext == ".obj":
                    wire_item = self._mgl_add_obj_wire_item(path, bool(self._mgl_wireframe))
                elif ext == ".fbx":
                    wire_item = self._mgl_add_fbx_wire_item(
                        path,
                        bool(self._mgl_wireframe) and (not bool(fbx_bind_joints_only)),
                        rig_context=fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                    )
                if wire_item is not None:
                    scene.add(wire_item)
                    if model_item is not None:
                        model_item.payload["edge_wire"] = True
                if ext == ".fbx":
                    self._mgl_fbx_joints_log(
                        "load_mesh wire "
                        + f"path={path} added={bool(wire_item is not None)} "
                        + f"wire_visible={bool(getattr(wire_item, 'visible', False)) if wire_item is not None else False} "
                        + f"bind_only={bool(fbx_bind_joints_only)}"
                    )
                if ext == ".fbx" and isinstance(fbx_rig_context, dict):
                    joint_overlays: List[MGLSceneItem] = []
                    capture_debug_enabled, animated_debug_enabled = self._mgl_fbx_joint_debug_modes(
                        fbx_rig_context
                    )
                    if capture_debug_enabled:
                        capture_item = self._mgl_add_fbx_joint_overlay_item(
                            path,
                            True,
                            pose_mode="capture",
                            owner=None,
                            path_key=str(path),
                            rig_context=fbx_rig_context,
                        )
                        if capture_item is not None:
                            capture_item.order = 16
                            joint_overlays.append(capture_item)
                    if animated_debug_enabled:
                        animated_item = self._mgl_add_fbx_joint_overlay_item(
                            path,
                            True,
                            pose_mode="animated",
                            owner=None,
                            path_key=str(path),
                            rig_context=fbx_rig_context,
                        )
                        if animated_item is not None:
                            animated_item.order = 16
                            joint_overlays.append(animated_item)
                    joint_bounds_min = None
                    joint_bounds_max = None
                    for overlay_item in joint_overlays:
                        scene.add(overlay_item)
                        try:
                            payload = dict(getattr(overlay_item, "payload", None) or {})
                            bmin = np.asarray(payload.get("bounds_min"), dtype="f4").reshape(-1)
                            bmax = np.asarray(payload.get("bounds_max"), dtype="f4").reshape(-1)
                            if bmin.size >= 3 and bmax.size >= 3:
                                b0 = bmin[:3].astype("f4", copy=False)
                                b1 = bmax[:3].astype("f4", copy=False)
                                if joint_bounds_min is None or joint_bounds_max is None:
                                    joint_bounds_min, joint_bounds_max = b0.copy(), b1.copy()
                                else:
                                    joint_bounds_min = np.minimum(joint_bounds_min, b0)
                                    joint_bounds_max = np.maximum(joint_bounds_max, b1)
                        except Exception:
                            pass
                    if (
                        bool(fbx_bind_joints_only)
                        and joint_bounds_min is not None
                        and joint_bounds_max is not None
                    ):
                        try:
                            self._mgl_init_arcball(
                                np.array([joint_bounds_min, joint_bounds_max], dtype="f4")
                            )
                        except Exception:
                            pass
                    self._mgl_fbx_joints_log(
                        "load_mesh overlays "
                        + f"path={path} count={int(len(joint_overlays))} "
                        + f"capture={bool(capture_debug_enabled)} "
                        + f"animated={bool(animated_debug_enabled)} "
                        + f"bind_only={bool(fbx_bind_joints_only)}"
                    )
                elif ext == ".fbx":
                    self._mgl_fbx_joints_log(
                        "load_mesh overlays skipped "
                        + f"path={path} reason=no_context"
                    )
                if ext == ".fbx":
                    try:
                        rig_count = 0
                        model_count = 0
                        wire_count = 0
                        for it in scene.items():
                            tag = str(getattr(it, "tag", "") or "")
                            if tag == "scene-rig-joints":
                                rig_count += 1
                            elif tag in {"model", "scene-model"}:
                                model_count += 1
                            elif tag in {"model-wire", "scene-wire"}:
                                wire_count += 1
                        self._mgl_fbx_joints_log(
                            "load_mesh done "
                            + f"path={path} rig_items={rig_count} model_items={model_count} wire_items={wire_count}"
                        )
                    except Exception:
                        pass
            self._mgl_mesh_path = str(path)
        except Exception as exc:
            self._mgl_error = f"Mesh upload failed: {exc}"
        finally:
            try:
                self.doneCurrent()
            except Exception:
                pass

    def _mgl_load_scene_assets(self, assets: List[Dict[str, str]], frame: bool = True) -> None:
        if self._mgl_ctx is None:
            self._mgl_error = "ModernGL context not ready"
            return
        self._mgl_fbx_joints_log_enabled = False
        try:
            for entry in assets or []:
                if not isinstance(entry, dict):
                    continue
                if bool(entry.get("fbx_debug_log", False)):
                    self._mgl_fbx_joints_log_enabled = True
                    break
        except Exception:
            self._mgl_fbx_joints_log_enabled = False
        try:
            self._mgl_fbx_joints_log(
                "=============================== scene_load =============================== "
                + f"asset_count={int(len(list(assets or [])))} frame={bool(frame)}"
            )
        except Exception:
            pass
        scene = getattr(self, "_mgl_scene", None)
        if scene is None:
            self._mgl_error = "Scene assembly not ready"
            return
        if np is None:
            self._mgl_error = "numpy unavailable"
            return

        def _merge_bounds(bmin, bmax, new_min, new_max):
            if new_min is None or new_max is None:
                return bmin, bmax
            if bmin is None or bmax is None:
                return new_min.copy(), new_max.copy()
            return np.minimum(bmin, new_min), np.maximum(bmax, new_max)

        def _normalize_asset_xform(raw):
            if not isinstance(raw, dict):
                return None

            def _triplet(value, default):
                seq = value if isinstance(value, (list, tuple)) else default
                try:
                    return (float(seq[0]), float(seq[1]), float(seq[2]))
                except Exception:
                    return (float(default[0]), float(default[1]), float(default[2]))

            return {
                "pos": _triplet(raw.get("pos", (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
                "rot": _triplet(raw.get("rot", (0.0, 0.0, 0.0)), (0.0, 0.0, 0.0)),
                "scl": _triplet(raw.get("scl", (1.0, 1.0, 1.0)), (1.0, 1.0, 1.0)),
            }

        def _xform_is_identity(xf) -> bool:
            if not isinstance(xf, dict):
                return False
            try:
                pos = xf.get("pos", (0.0, 0.0, 0.0))
                rot = xf.get("rot", (0.0, 0.0, 0.0))
                scl = xf.get("scl", (1.0, 1.0, 1.0))
                return (
                    all(abs(float(v)) < 1.0e-6 for v in pos)
                    and all(abs(float(v)) < 1.0e-6 for v in rot)
                    and all(abs(float(v) - 1.0) < 1.0e-6 for v in scl)
                )
            except Exception:
                return False

        def _seed_asset_xform(owner_name: str, raw_xform, *, is_splat: bool = False) -> None:
            owner_key = str(owner_name or "").strip()
            xf = _normalize_asset_xform(raw_xform)
            if not owner_key or not isinstance(xf, dict):
                return
            attr = "_mgl_scene_splat_xforms_by_owner" if is_splat else "_mgl_scene_xforms_by_owner"
            mapping = getattr(self, attr, None)
            if not isinstance(mapping, dict):
                mapping = {}
                setattr(self, attr, mapping)
            existing = self._mgl_casefold_get(mapping, owner_key)
            if isinstance(existing, dict) and _xform_is_identity(xf) and not _xform_is_identity(existing):
                return
            mapping[owner_key] = xf

        self._mgl_error = ""
        # Reset scene scale for every load (snapshots should not override this).
        try:
            self._mgl_scale_multiplier = 1.0
            label = getattr(self, "_example_scale_label", None)
            if label is not None:
                label.setText("Scale 1.00x")
            slider = getattr(self, "_example_scale_slider", None)
            if slider is not None:
                try:
                    slider.blockSignals(True)
                    slider.setValue(100)
                finally:
                    slider.blockSignals(False)
        except Exception:
            pass
        self._mgl_clear_scene_models()
        self._mgl_submeshes = []
        self._mgl_vao = None
        self._mgl_mesh_vbos = []
        self._mgl_index_buffer = None
        self._mgl_mesh = None
        self._mgl_mesh_vertex_count = 0
        self._mgl_mesh_path = ""
        self._mgl_set_uv_overlay(None)
        self._mgl_scene_uvs_by_owner = {}
        self._mgl_scene_tex_by_owner = {}
        self._mgl_scene_proc_textures_by_owner = {}
        self._mgl_scene_volume_overrides_by_owner = {}
        self._mgl_scene_render_proxy_by_owner = {}
        self._mgl_scene_skinned_splat_proxies_by_owner = {}
        self._mgl_material_log_enabled = False
        self._mgl_material_debug_owners = set()
        self._mgl_fx_log_enabled = False
        self._mgl_proc_provider = None
        self._mgl_proc_label = ""
        self._mgl_proc_rev = -1
        # prevent texture leaking from previous "Texture..." or textured Import views
        self._mgl_texture_override = False
        self._mgl_texture = None
        self._mgl_texture_path = ""
        self._mgl_texture_paths = []
        self._mgl_retarget_preview_active = False
        self._mgl_retarget_preview_owner = ""
        self._mgl_retarget_node_item = None
        self._mgl_retarget_node_model = None
        self._mgl_retarget_joint_handles_by_owner = {}
        self._mgl_retarget_joint_positions = {}
        self._mgl_retarget_joint_map = {}
        self._mgl_retarget_handle_radius = 0.008
        self._mgl_retarget_curve_thickness = 2.4
        self._mgl_retarget_role_owners = {}
        self._mgl_retarget_handle_frame_keys = {}
        self._mgl_retarget_selected_joint = None
        self._mgl_retarget_last_pick_mode = ""
        self._mgl_retarget_selection_dirty = False
        self._mgl_retarget_links_dirty = False
        self._mgl_scene_skeleton_active_owner = str(getattr(self, "_mgl_scene_skeleton_active_owner", "") or "")
        self._mgl_scene_skeleton_selected_joint = str(getattr(self, "_mgl_scene_skeleton_selected_joint", "") or "")
        self._mgl_scene_skeleton_handles_by_owner = {}
        self._mgl_scene_skeleton_handle_frame_keys = {}
        try:
            for entry in assets or []:
                if not isinstance(entry, dict):
                    continue
                if not bool(entry.get("debug_log", False)):
                    continue
                kind = str(entry.get("kind") or "").strip().lower()
                if kind == "fx_trail":
                    self._mgl_fx_log_enabled = True
                    continue
                owner = str(entry.get("node") or "").strip()
                if owner:
                    self._mgl_material_debug_owners.add(owner)
                    self._mgl_material_log_enabled = True
        except Exception:
            pass

        # Ensure offset-mode owners are always registered for this scene load.
        splat_zero_pivot_owners = set()
        try:
            offset_map = {}
            for entry in assets or []:
                if not isinstance(entry, dict):
                    continue
                if not entry.get("xform_offset"):
                    continue
                name = (entry.get("node") or "").strip()
                if not name:
                    path_str = str(entry.get("path", "") or "").strip()
                    if path_str:
                        name = Path(path_str).name
                if not name:
                    continue
                offset_map[name] = True
                try:
                    ext_hint = str(entry.get("ext") or "").strip().lower()
                    if not ext_hint:
                        ext_hint = Path(str(entry.get("path", "") or "").strip()).suffix.lower()
                    if ext_hint == ".ply" and bool(entry.get("splat_zero_pivot", False)):
                        splat_zero_pivot_owners.add(str(name))
                except Exception:
                    pass
            self._mgl_scene_xform_offset_by_owner = offset_map
        except Exception:
            pass

        try:
            prev_mesh_xforms = (
                getattr(self, "_mgl_scene_xforms_by_owner", None)
                if isinstance(getattr(self, "_mgl_scene_xforms_by_owner", None), dict)
                else {}
            )
            prev_splat_xforms = (
                getattr(self, "_mgl_scene_splat_xforms_by_owner", None)
                if isinstance(getattr(self, "_mgl_scene_splat_xforms_by_owner", None), dict)
                else {}
            )
            self._mgl_scene_splats = {}
            self._mgl_scene_bounds_by_owner = {}
            self._mgl_scene_splats_world = {}
            self._mgl_scene_splat_glow_by_owner = {}
            self._mgl_scene_splats_bounds_local = {}
            self._mgl_scene_splat_bounds_by_owner = {}
            self._mgl_scene_mesh_bounds_by_owner = {}
            self._mgl_scene_pivot_local_by_owner = {}
            self._mgl_scene_skinned_splat_proxies_by_owner = {}
            # Preserve xforms loaded from workflow
            self._mgl_scene_xforms_by_owner = prev_mesh_xforms
            self._mgl_scene_splat_xforms_by_owner = prev_splat_xforms
        except Exception:
            pass

        # Some sequence-driven splats should always rotate/scale around local origin.
        try:
            if splat_zero_pivot_owners:
                piv = getattr(self, "_mgl_scene_pivot_local_by_owner", None)
                if not isinstance(piv, dict):
                    piv = {}
                for owner_name in splat_zero_pivot_owners:
                    piv[str(owner_name)] = (0.0, 0.0, 0.0)
                self._mgl_scene_pivot_local_by_owner = piv
        except Exception:
            pass

        bounds_min = None
        bounds_max = None
        has_mesh_bounds = False
        has_splats = False
        total_indices = 0
        first_mesh_path = ""
        mesh_owner_names = set()
        try:
            for asset in assets or []:
                if not isinstance(asset, dict):
                    continue
                kind = str(asset.get("kind") or "").strip().lower()
                ext_hint = str(asset.get("ext") or "").strip().lower()
                if kind in {"camera", "light"} or ext_hint in {".camera", ".light"}:
                    continue
                if bool(asset.get("wire_only")) or bool(asset.get("volume")):
                    continue
                if not bool(asset.get("visible", True)):
                    continue
                path_str = str(asset.get("path", "") or "").strip()
                if not path_str:
                    continue
                ext = Path(path_str).suffix.lower()
                if ext == ".ply":
                    continue
                owner_name = str(asset.get("node") or "").strip()
                if not owner_name:
                    owner_name = Path(path_str).name
                if owner_name:
                    mesh_owner_names.add(owner_name)
        except Exception:
            mesh_owner_names = set()

        did_make_current = False
        try:
            self.makeCurrent()
            did_make_current = True

            for asset in assets or []:
                if asset.get("volume_override"):
                    target_owner = str(asset.get("target_owner") or "").strip()
                    volume_owner = str(asset.get("volume_owner") or "").strip()
                    if not target_owner or not volume_owner:
                        continue
                    override_entry: dict = {"volume_owner": volume_owner}
                    provider = asset.get("texture_provider")
                    tex_override = None
                    if provider is not None:
                        gpu_state = self._mgl_proc_state(provider)
                        if gpu_state:
                            override_entry["provider"] = provider
                            override_entry["gpu"] = True
                            override_entry["gpu_state"] = gpu_state
                            try:
                                self._mgl_ensure_proc_glyph(gpu_state)
                            except Exception:
                                pass
                        else:
                            qimg = self._mgl_provider_image(provider)
                            if qimg is not None and not qimg.isNull():
                                if hasattr(QtGui.QImage, "Format_RGBA8888"):
                                    qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                                else:
                                    qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                                qimg = qimg.mirrored(False, True)
                                try:
                                    tex_override = self._mgl_make_texture(qimg)
                                except Exception:
                                    tex_override = None
                                if tex_override is not None:
                                    override_entry["provider"] = provider
                                    override_entry["texture"] = tex_override
                                    try:
                                        override_entry["rev"] = int(getattr(provider, "revision", 0))
                                    except Exception:
                                        pass
                    if tex_override is None and not override_entry.get("gpu"):
                        texture_path = str(asset.get("texture", "") or "").strip()
                        if texture_path:
                            try:
                                tex_path = Path(texture_path)
                            except Exception:
                                tex_path = None
                            if tex_path is not None and tex_path.exists():
                                qimg = QtGui.QImage(str(tex_path))
                                if not qimg.isNull():
                                    if hasattr(QtGui.QImage, "Format_RGBA8888"):
                                        qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                                    else:
                                        qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                                    qimg = qimg.mirrored(False, True)
                                    try:
                                        tex_override = self._mgl_make_texture(qimg)
                                    except Exception:
                                        tex_override = None
                                    if tex_override is not None:
                                        override_entry["texture"] = tex_override
                    if override_entry.get("gpu") or override_entry.get("texture") is not None:
                        try:
                            store = self._mgl_scene_volume_overrides_by_owner.setdefault(target_owner, [])
                            store.append(override_entry)
                        except Exception:
                            pass
                    continue

                kind = str(asset.get("kind") or "").strip().lower()
                ext_hint = str(asset.get("ext") or "").strip().lower()
                material = self._mgl_normalize_material(asset.get("material"))
                if kind == "anim_retarget_preview":
                    retarget_bounds = self._mgl_retarget_load_preview_asset(asset)
                    if retarget_bounds is not None:
                        try:
                            rbmin, rbmax = retarget_bounds
                            bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, rbmin, rbmax)
                            has_mesh_bounds = True
                            has_splats = True
                        except Exception:
                            pass
                    continue
                if kind == "fx_trail":
                    target_owner = str(asset.get("target_owner") or "").strip()
                    owner = str(asset.get("node") or "").strip() or (f"{target_owner}_fx" if target_owner else "")
                    if not target_owner or not owner:
                        self._mgl_fx_log(
                            f"[renderer] load skip invalid-asset owner={owner!r} "
                            f"target_owner={target_owner!r} asset={asset!r}"
                        )
                        continue
                    trail_item = MGLSceneItem(
                        name=owner,
                        draw_fn=MGLRendererMixin._mgl_draw_scene_fx_trail,
                        payload={
                            "owner": owner,
                            "material": dict(asset.get("instance_material") or {}) if isinstance(asset.get("instance_material"), dict) else None,
                            "debug_log": bool(asset.get("debug_log", False)),
                            "target_owner": target_owner,
                            "target_owner_aliases": list(asset.get("target_owner_aliases") or []),
                            "instance_path": str(asset.get("instance_path") or "").strip(),
                            "instance_source_name": str(asset.get("instance_source_name") or "").strip(),
                            "instance_material": dict(asset.get("instance_material") or {}) if isinstance(asset.get("instance_material"), dict) else None,
                            "instance_texture": str(asset.get("instance_texture") or "").strip(),
                            "instance_texture_provider": asset.get("instance_texture_provider"),
                            "instance_xform": dict(asset.get("instance_xform") or {}) if isinstance(asset.get("instance_xform"), dict) else None,
                            "enabled": bool(asset.get("enabled", True)),
                            "global_space": bool(asset.get("global_space", False)),
                            "samples": int(asset.get("samples", 28) or 28),
                            "frame_step": int(asset.get("frame_step", 1) or 1),
                            "spawn_rate": float(asset.get("spawn_rate", asset.get("frame_step", 1.0)) or 1.0),
                            "substeps": int(asset.get("substeps", 1) or 1),
                            "lifespan": int(asset.get("lifespan", max(1, int(asset.get("samples", 28) or 28) * int(asset.get("frame_step", 1) or 1))) or 1),
                            "repeats": int(asset.get("repeats", 1) or 1),
                            "radius": float(asset.get("radius", 0.35) or 0.35),
                            "sides": int(asset.get("sides", 28) or 28),
                            "color": self._mgl_fx_color_rgba(asset.get("color")),
                            "line_width": float(asset.get("line_width", 2.0) or 2.0),
                            "profile_points": list(asset.get("profile_points") or []),
                            "age_scale_min": float(asset.get("age_scale_min", 1.0) or 1.0),
                            "age_scale_max": float(asset.get("age_scale_max", 1.0) or 1.0),
                            "age_scale_points": list(asset.get("age_scale_points") or []),
                        },
                        resources=[],
                    visible=bool(asset.get("visible", True)),
                    order=14,
                    tag="scene-fx-trail",
                )
                    scene.add(trail_item)
                    self._mgl_fx_log(
                        f"[renderer] load item owner={owner} target_owner={target_owner} "
                        f"instance={str(asset.get('instance_source_name') or '').strip() or str(asset.get('instance_path') or '').strip() or '<rings>'} "
                        f"visible={bool(asset.get('visible', True))} enabled={bool(asset.get('enabled', True))} "
                        f"global={bool(asset.get('global_space', False))} repeats={int(asset.get('repeats', 1) or 1)} "
                        f"samples={int(asset.get('samples', 28) or 28)} frame_step={int(asset.get('frame_step', 1) or 1)} "
                        f"spawn_rate={float(asset.get('spawn_rate', asset.get('frame_step', 1.0)) or 1.0):.3f} "
                        f"substeps={int(asset.get('substeps', 1) or 1)} "
                        f"lifespan={int(asset.get('lifespan', max(1, int(asset.get('samples', 28) or 28) * int(asset.get('frame_step', 1) or 1))) or 1)} "
                        f"radius={float(asset.get('radius', 0.35) or 0.35):.4f} "
                        f"sides={int(asset.get('sides', 28) or 28)} aliases={list(asset.get('target_owner_aliases') or [])!r}"
                    )
                    continue
                is_camera = kind == "camera" or ext_hint == ".camera"
                is_light = kind == "light" or ext_hint == ".light"
                path_str = str(asset.get("path", "") or "").strip()
                path = None
                ext = ext_hint
                owner = str(asset.get("node") or "").strip()
                if path_str:
                    path = Path(path_str)
                    if not path.exists():
                        if material is not None:
                            self._mgl_material_log(
                                "renderer.load.skip_missing_path",
                                owner=owner,
                                path=path_str,
                                kind=kind,
                                ext=ext_hint,
                                material=material,
                            )
                        continue
                    if not ext:
                        ext = path.suffix.lower()
                    if not owner:
                        owner = path.name
                elif not (is_camera or is_light):
                    if material is not None:
                        self._mgl_material_log(
                            "renderer.load.skip_no_path",
                            owner=owner,
                            kind=kind,
                            ext=ext_hint,
                            material=material,
                        )
                    continue
                if not owner:
                    if material is not None:
                        self._mgl_material_log(
                            "renderer.load.skip_no_owner",
                            path=str(path) if path is not None else "",
                            kind=kind,
                            ext=ext,
                            material=material,
                        )
                    continue
                if path is not None:
                    path_key = str(path)
                elif is_light:
                    path_key = f"light://{owner}"
                else:
                    path_key = f"camera://{owner}"
                visibility_map = getattr(self, "_mgl_scene_visibility", {}) or {}
                visible = bool(visibility_map.get(owner, True))
                wire_only = bool(asset.get("wire_only"))
                is_volume = bool(asset.get("volume"))
                render_proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else None
                copy_to_points = asset.get("copy_to_points") if isinstance(asset.get("copy_to_points"), dict) else None
                gpu_copy_instances = bool(copy_to_points.get("gpu_instances", False)) if isinstance(copy_to_points, dict) else False
                music_effects = asset.get("music_effects") if isinstance(asset.get("music_effects"), dict) else None
                proxy_type = str((render_proxy or {}).get("type") or "").strip().lower() if isinstance(render_proxy, dict) else ""
                if isinstance(render_proxy, dict) and isinstance(music_effects, dict) and proxy_type in {"skinned_splat", "skinned_gaussian_splat"}:
                    render_proxy = dict(render_proxy)
                    if not isinstance(render_proxy.get("splat_fx"), dict):
                        converted_splat_fx = self._mgl_splat_fx_from_music_effects(music_effects)
                        if isinstance(converted_splat_fx, dict):
                            render_proxy["splat_fx"] = converted_splat_fx
                    proxy_type = str(render_proxy.get("type") or "").strip().lower()
                _seed_asset_xform(
                    owner,
                    asset.get("xform"),
                    is_splat=bool(ext == ".ply" or proxy_type in {"skinned_splat", "skinned_gaussian_splat"}),
                )
                if isinstance(render_proxy, dict):
                    try:
                        self._mgl_scene_render_proxy_by_owner[owner] = dict(render_proxy)
                    except Exception:
                        pass
                if is_light:
                    light_cfg = asset.get("light") if isinstance(asset.get("light"), dict) else {}
                    light_type = self._mgl_normalize_light_type(light_cfg.get("type", "directional"))
                    if bool(visible) and not str(getattr(self, "_mgl_scene_light_owner", "") or "").strip():
                        try:
                            self._mgl_scene_light_owner = owner
                        except Exception:
                            pass
                        try:
                            self._mgl_scene_light_type = light_type
                        except Exception:
                            self._mgl_scene_light_type = "directional"
                        try:
                            self._mgl_scene_light_intensity = max(0.0, float(light_cfg.get("intensity", 1.0)))
                        except Exception:
                            self._mgl_scene_light_intensity = 1.0
                        try:
                            self._mgl_scene_light_range = max(0.0, float(light_cfg.get("range", 0.0)))
                        except Exception:
                            self._mgl_scene_light_range = 0.0
                        try:
                            self._mgl_scene_light_shadow_strength = max(
                                0.0,
                                min(1.0, float(light_cfg.get("shadow_strength", 1.0))),
                            )
                        except Exception:
                            self._mgl_scene_light_shadow_strength = 1.0
                        try:
                            self._mgl_scene_light_shadow_range = max(
                                0.0,
                                float(light_cfg.get("shadow_range", 0.0)),
                            )
                        except Exception:
                            self._mgl_scene_light_shadow_range = 0.0
                        try:
                            raw_fov = float(light_cfg.get("shadow_fov", 0.0))
                            self._mgl_scene_light_shadow_fov = (
                                max(1.0, min(179.0, raw_fov)) if raw_fov > 0.0 else 0.0
                            )
                        except Exception:
                            self._mgl_scene_light_shadow_fov = 0.0
                        try:
                            self._mgl_scene_light_shadow_near = max(
                                0.0,
                                float(light_cfg.get("shadow_near", 0.0)),
                            )
                        except Exception:
                            self._mgl_scene_light_shadow_near = 0.0
                        try:
                            self._mgl_scene_light_shadow_bias = max(
                                0.0,
                                float(light_cfg.get("shadow_bias", 0.0)),
                            )
                        except Exception:
                            self._mgl_scene_light_shadow_bias = 0.0
                        try:
                            xf = self._mgl_get_scene_asset_xform(owner)
                            pos = list(xf.get("pos", (0.0, 0.0, 0.0)))[:3] if isinstance(xf, dict) else (0.0, 0.0, 0.0)
                            self._mgl_scene_light_pos = tuple(float(v) for v in pos)
                        except Exception:
                            self._mgl_scene_light_pos = (0.0, 0.0, 0.0)

                    wire_item = None
                    try:
                        line_points = self._mgl_light_guide_line_points(light_type)
                        if line_points is None or line_points.size == 0:
                            raise ValueError("empty light guide")
                        self._mgl_scene_bounds_by_owner[owner] = (
                            line_points.min(axis=0).astype("f4"),
                            line_points.max(axis=0).astype("f4"),
                        )
                        self._mgl_scene_mesh_bounds_by_owner[owner] = self._mgl_scene_bounds_by_owner[owner]
                        wire_item = self._mgl_add_wire_item_from_points(
                            name=f"{owner}-light",
                            line_points=line_points,
                            visible=visible,
                            tag="scene-light",
                            owner=owner,
                            path_key=path_key,
                        )
                    except Exception:
                        wire_item = None
                    if wire_item is not None:
                        payload = wire_item.payload or {}
                        payload["color"] = (1.0, 0.92, 0.25, 1.0)
                        payload["line_width"] = 2.2
                        light_payload = dict(light_cfg)
                        light_payload["type"] = light_type
                        payload["light"] = light_payload
                        wire_item.payload = payload
                        scene.add(wire_item)
                    continue
                fbx_rig_context = asset.get("fbx_rig_context") if isinstance(asset, dict) else None
                if (
                    not isinstance(fbx_rig_context, dict)
                    and ext in {".fbx", ".bvh"}
                    and path is not None
                ):
                    try:
                        fbx_rig_context = self._mgl_fbx_rig_context_for_path(path)
                    except Exception:
                        fbx_rig_context = None
                skinned_proxy_loaded = False
                if isinstance(render_proxy, dict):
                    proxy_bounds = self._mgl_load_skinned_splat_proxy(
                        owner,
                        render_proxy,
                        fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                    )
                    if proxy_bounds is not None:
                        skinned_proxy_loaded = True
                        has_splats = True
                        try:
                            pmin, pmax = proxy_bounds
                            bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, pmin, pmax)
                            has_mesh_bounds = True
                        except Exception:
                            pass
                hide_source_for_proxy = bool(
                    skinned_proxy_loaded
                    and ext == ".fbx"
                    and bool(render_proxy.get("hide_source_mesh", True) if isinstance(render_proxy, dict) else True)
                )
                fbx_bind_joints_only = bool(
                    ext == ".fbx"
                    and self._mgl_fbx_bind_joints_only_enabled(
                        fbx_rig_context if isinstance(fbx_rig_context, dict) else None
                    )
                )
                model_visible = bool(visible) and (not bool(fbx_bind_joints_only)) and (not bool(hide_source_for_proxy))
                contribute_mesh_bounds = (not bool(fbx_bind_joints_only)) and (not bool(hide_source_for_proxy))
                if ext in {".fbx", ".bvh"}:
                    context = fbx_rig_context if isinstance(fbx_rig_context, dict) else {}
                    self._mgl_fbx_joints_log(
                        "scene_asset begin "
                        + f"owner={owner} path={path} visible={bool(visible)} "
                        + f"has_context={bool(isinstance(fbx_rig_context, dict))} "
                        + f"has_skeleton={bool(context.get('skeleton') is not None)} "
                        + f"capture={bool(context.get('show_capture_joints', False))} "
                        + f"animated={bool(context.get('show_animated_joints', False))} "
                        + f"bind_only={bool(fbx_bind_joints_only)} model_visible={bool(model_visible)} "
                        + f"skinned_proxy={bool(skinned_proxy_loaded)} hide_source={bool(hide_source_for_proxy)}"
                    )
                if hide_source_for_proxy:
                    if not first_mesh_path and path is not None:
                        first_mesh_path = str(path)
                    continue

                if is_camera:
                    # Preferred camera proxy path: an OBJ generated from primitive cube+cone,
                    # rendered with the same volume-wire pass used by split-volume guides.
                    if path is not None and ext == ".obj":
                        bmin = bmax = None
                        try:
                            model_data = load_model(path)
                            if model_data is not None and model_data.vertices:
                                points = np.array(model_data.vertices, dtype="f4").reshape(-1, 3)
                                bmin = points.min(axis=0)
                                bmax = points.max(axis=0)
                        except Exception:
                            bmin = bmax = None
                        if bmin is not None and bmax is not None:
                            try:
                                self._mgl_scene_bounds_by_owner[owner] = (bmin.astype("f4"), bmax.astype("f4"))
                                self._mgl_scene_mesh_bounds_by_owner[owner] = (bmin.astype("f4"), bmax.astype("f4"))
                                try:
                                    # Camera gizmo pivot: large lens ring center at front (min Z).
                                    cx, cy, cz = self._mgl_camera_front_ring_pivot_local(points, bmin, bmax)
                                    piv = getattr(self, "_mgl_scene_pivot_local_by_owner", None)
                                    if not isinstance(piv, dict):
                                        piv = {}
                                        self._mgl_scene_pivot_local_by_owner = piv
                                    piv[owner] = (cx, cy, cz)
                                except Exception:
                                    pass
                            except Exception:
                                pass

                        wire_item = self._mgl_add_obj_wire_item(
                            path,
                            visible,
                            tag="scene-volume",
                            owner=owner,
                            path_key=path_key,
                            outline_only=True,
                        )
                        if wire_item is not None:
                            payload = wire_item.payload or {}
                            payload["color"] = (0.95, 0.82, 0.27, 1.0)
                            wire_item.payload = payload
                            scene.add(wire_item)
                            if not first_mesh_path:
                                first_mesh_path = str(path)
                        continue

                    # Fallback path for pathless camera proxies.
                    try:
                        line_points = np.array(debug_camera_wire_vertices(), dtype="f4").reshape(-1, 3)
                    except Exception:
                        line_points = None
                    if line_points is None or line_points.size == 0:
                        continue
                    try:
                        bmin = line_points.min(axis=0)
                        bmax = line_points.max(axis=0)
                        self._mgl_scene_bounds_by_owner[owner] = (bmin.astype("f4"), bmax.astype("f4"))
                        self._mgl_scene_mesh_bounds_by_owner[owner] = (bmin.astype("f4"), bmax.astype("f4"))
                        try:
                            cx, cy, cz = self._mgl_camera_front_ring_pivot_local(line_points, bmin, bmax)
                            piv = getattr(self, "_mgl_scene_pivot_local_by_owner", None)
                            if not isinstance(piv, dict):
                                piv = {}
                                self._mgl_scene_pivot_local_by_owner = piv
                            piv[owner] = (cx, cy, cz)
                        except Exception:
                            pass
                    except Exception:
                        pass
                    camera_item = self._mgl_add_wire_item_from_points(
                        name=f"{owner}-camera",
                        line_points=line_points,
                        visible=visible,
                        tag="scene-camera",
                        owner=owner,
                        path_key=path_key,
                    )
                    if camera_item is not None:
                        payload = camera_item.payload or {}
                        payload["color"] = (0.95, 0.82, 0.27, 1.0)
                        if "fov" in asset:
                            payload["fov"] = asset.get("fov")
                        camera_item.payload = payload
                        scene.add(camera_item)
                    continue

                if ext == ".bvh":
                    context = fbx_rig_context if isinstance(fbx_rig_context, dict) else None
                    if not isinstance(context, dict) or path is None:
                        continue
                    capture_debug_enabled, animated_debug_enabled = self._mgl_fbx_joint_debug_modes(context)
                    if not capture_debug_enabled and not animated_debug_enabled:
                        animated_debug_enabled = True
                    joint_overlays: List[MGLSceneItem] = []
                    if capture_debug_enabled:
                        capture_item = self._mgl_add_fbx_joint_overlay_item(
                            path,
                            bool(visible),
                            pose_mode="capture",
                            owner=owner,
                            path_key=path_key,
                            rig_context=context,
                        )
                        if capture_item is not None:
                            capture_item.order = 16
                            joint_overlays.append(capture_item)
                    if animated_debug_enabled:
                        animated_item = self._mgl_add_fbx_joint_overlay_item(
                            path,
                            bool(visible),
                            pose_mode="animated",
                            owner=owner,
                            path_key=path_key,
                            rig_context=context,
                        )
                        if animated_item is not None:
                            animated_item.order = 16
                            joint_overlays.append(animated_item)

                    joint_bounds_min = None
                    joint_bounds_max = None
                    for overlay_item in joint_overlays:
                        scene.add(overlay_item)
                        try:
                            payload = dict(getattr(overlay_item, "payload", None) or {})
                            bmin = np.asarray(payload.get("bounds_min"), dtype="f4").reshape(-1)
                            bmax = np.asarray(payload.get("bounds_max"), dtype="f4").reshape(-1)
                            if bmin.size >= 3 and bmax.size >= 3:
                                b0 = bmin[:3].astype("f4", copy=False)
                                b1 = bmax[:3].astype("f4", copy=False)
                                if joint_bounds_min is None or joint_bounds_max is None:
                                    joint_bounds_min, joint_bounds_max = b0.copy(), b1.copy()
                                else:
                                    joint_bounds_min = np.minimum(joint_bounds_min, b0)
                                    joint_bounds_max = np.maximum(joint_bounds_max, b1)
                        except Exception:
                            pass
                    if joint_bounds_min is not None and joint_bounds_max is not None:
                        try:
                            self._mgl_scene_bounds_by_owner[owner] = (
                                joint_bounds_min.astype("f4"),
                                joint_bounds_max.astype("f4"),
                            )
                            self._mgl_scene_mesh_bounds_by_owner[owner] = (
                                joint_bounds_min.astype("f4"),
                                joint_bounds_max.astype("f4"),
                            )
                        except Exception:
                            pass
                        bounds_min, bounds_max = _merge_bounds(
                            bounds_min,
                            bounds_max,
                            joint_bounds_min,
                            joint_bounds_max,
                        )
                        has_mesh_bounds = True
                    self._mgl_fbx_joints_log(
                        "scene_asset bvh_overlays "
                        + f"owner={owner} path={path} count={int(len(joint_overlays))} "
                        + f"capture={bool(capture_debug_enabled)} "
                        + f"animated={bool(animated_debug_enabled)}"
                    )
                    continue

                if wire_only:
                    bmin = bmax = None
                    if is_volume:
                        try:
                            bmin = np.array([-0.5, -0.5, -0.5], dtype=np.float32)
                            bmax = np.array([0.5, 0.5, 0.5], dtype=np.float32)
                        except Exception:
                            bmin = bmax = None
                    if bmin is None or bmax is None:
                        try:
                            model_data = load_model(path)
                            if model_data is not None and model_data.vertices:
                                points = np.array(model_data.vertices, dtype="f4").reshape(-1, 3)
                                bmin = points.min(axis=0)
                                bmax = points.max(axis=0)
                        except Exception:
                            bmin = bmax = None
                    if bmin is not None and bmax is not None:
                        try:
                            self._mgl_scene_bounds_by_owner[owner] = (bmin.astype("f4"), bmax.astype("f4"))
                            self._mgl_scene_mesh_bounds_by_owner[owner] = (bmin.astype("f4"), bmax.astype("f4"))
                        except Exception:
                            pass

                    wire_item = None
                    if ext == ".obj":
                        wire_item = self._mgl_add_obj_wire_item(
                            path,
                            visible,
                            tag="scene-volume" if is_volume else "scene-wire",
                            owner=owner,
                            path_key=path_key,
                            outline_only=bool(is_volume),
                        )
                    elif ext == ".fbx":
                        wire_item = self._mgl_add_fbx_wire_item(
                            path,
                            visible,
                            tag="scene-volume" if is_volume else "scene-wire",
                            owner=owner,
                            path_key=path_key,
                            rig_context=fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                        )
                    if wire_item is not None:
                        scene.add(wire_item)
                        if not first_mesh_path:
                            first_mesh_path = str(path)
                    continue

                if ext == ".ply":
                    try:
                        from echograph.util.splats_io import load_splats_ply

                        try:
                            self._mgl_log(
                                "splats: load scene asset owner="
                                + str(owner)
                                + " visible="
                                + str(bool(visible))
                                + " path="
                                + str(path)
                            )
                        except Exception:
                            pass
                        splats = load_splats_ply(str(path), n=200_000)
                        arr = np.asarray(splats, dtype=np.float32)
                        if arr.ndim == 2 and arr.shape[1] in (8, 10, 14, 15):
                            has_splats = True
                            try:
                                self._mgl_scene_splats[owner] = arr
                            except Exception:
                                pass
                            try:
                                self._mgl_log("splats: loaded owner=" + str(owner) + " shape=" + str(arr.shape))
                            except Exception:
                                pass
                            mins = arr[:, :3].min(axis=0)
                            maxs = arr[:, :3].max(axis=0)
                            bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                            try:
                                self._mgl_scene_splat_bounds_by_owner[owner] = (mins.astype("f4"), maxs.astype("f4"))
                            except Exception:
                                pass
                            try:
                                self._mgl_scene_splats_bounds_local[owner] = (mins.astype("f4"), maxs.astype("f4"))
                            except Exception:
                                pass
                    except Exception as exc:
                        self._mgl_error = f"Splat load failed: {exc}"
                        try:
                            self._mgl_log("splats: load failed owner=" + str(owner) + " err=" + repr(exc))
                        except Exception:
                            pass
                    continue

                texture_override = None
                proc_provider = asset.get("texture_provider")
                if proc_provider is not None:
                    gpu_state = self._mgl_proc_state(proc_provider)
                    if gpu_state:
                        try:
                            self._mgl_scene_proc_textures_by_owner[owner] = {
                                "provider": proc_provider,
                                "texture": None,
                                "rev": int(getattr(proc_provider, "revision", 0)),
                                "subs": None,
                                "gpu": True,
                                "gpu_state": gpu_state,
                            }
                        except Exception:
                            pass
                    else:
                        qimg = self._mgl_provider_image(proc_provider)
                        if qimg is not None and not qimg.isNull():
                            if hasattr(QtGui.QImage, "Format_RGBA8888"):
                                qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                            else:
                                qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                            qimg = qimg.mirrored(False, True)
                            try:
                                texture_override = self._mgl_make_texture(qimg)
                            except Exception:
                                texture_override = None
                            if texture_override is not None:
                                try:
                                    self._mgl_scene_proc_textures_by_owner[owner] = {
                                        "provider": proc_provider,
                                        "texture": texture_override,
                                        "rev": int(getattr(proc_provider, "revision", 0)),
                                        "subs": None,
                                    }
                                except Exception:
                                    pass
                if texture_override is None:
                    texture_path = str(asset.get("texture", "") or "").strip()
                    if texture_path and not self._mgl_texture_override:
                        try:
                            tex_path = Path(texture_path)
                        except Exception:
                            tex_path = None
                        if tex_path is not None and tex_path.exists():
                            try:
                                self._mgl_scene_tex_by_owner[owner] = str(tex_path)
                            except Exception:
                                pass
                            qimg = QtGui.QImage(str(tex_path))
                            if not qimg.isNull():
                                if hasattr(QtGui.QImage, "Format_RGBA8888"):
                                    qimg = qimg.convertToFormat(QtGui.QImage.Format_RGBA8888)
                                else:
                                    qimg = qimg.convertToFormat(QtGui.QImage.Format_ARGB32)
                                qimg = qimg.mirrored(False, True)
                                try:
                                    texture_override = self._mgl_make_texture(qimg)
                                except Exception:
                                    texture_override = None

                mesh = None
                mesh_arrays = None
                points = None
                normals = None
                uvs = None
                if openmesh is not None and ext != ".fbx":
                    try:
                        mesh = openmesh.read_trimesh(str(path))
                    except Exception:
                        mesh = None

                if mesh is None:
                    if ext == ".fbx":
                        try:
                            mesh_arrays = load_fbx_mesh_arrays_pyassimp(path)
                            points = mesh_arrays.points
                            normals = mesh_arrays.normals
                            uvs = mesh_arrays.uvs
                        except Exception as exc:
                            mesh_arrays = self._mgl_fbx_mesh_arrays_from_context(
                                fbx_rig_context if isinstance(fbx_rig_context, dict) else None
                            )
                            if mesh_arrays is None:
                                self._mgl_error = f"FBX load failed: {exc}"
                                continue
                            points = mesh_arrays.points
                            normals = mesh_arrays.normals
                            uvs = mesh_arrays.uvs
                    elif ext == ".obj":
                        try:
                            points, normals, uvs = load_obj_mesh_arrays(path)
                        except Exception:
                            points = None
                    elif ext in (".gltf", ".glb"):
                        try:
                            mesh_arrays = load_gltf_mesh_arrays(path)
                            points = mesh_arrays.points
                            normals = mesh_arrays.normals
                            uvs = mesh_arrays.uvs
                        except Exception:
                            points = None

                    if points is None:
                        model_data = load_model(path)
                        if model_data is None or not model_data.vertices:
                            continue
                        points = np.array(model_data.vertices, dtype="f4").reshape(-1, 3)
                        normals = np.zeros_like(points)
                        for i in range(0, points.shape[0], 3):
                            a, b, c = points[i:i + 3]
                            n = np.cross(b - a, c - a)
                            norm = np.linalg.norm(n)
                            if norm > 1e-6:
                                n = n / norm
                            normals[i:i + 3] = n

                model_item = None
                wire_points = None
                if mesh is not None:
                    mesh.update_normals()
                    points = np.array(mesh.points(), dtype="f4")
                    normals = np.array(mesh.vertex_normals(), dtype="f4")
                    indices = np.array(mesh.face_vertex_indices(), dtype="u4").ravel()
                    wire_points = self._mgl_edge_vertices_from_mesh(points, indices)
                    entry = self._mgl_build_mesh_entry(points, normals, None, indices=indices)
                    if entry is None:
                        if material is not None:
                            self._mgl_material_log(
                                "renderer.load.skip_mesh_entry_none",
                                owner=owner,
                                path=str(path),
                                ext=ext,
                                material=material,
                            )
                        continue
                    resources = [
                        entry.get("vao"),
                        entry.get("vbo"),
                        entry.get("nbo"),
                        entry.get("tbo"),
                        entry.get("cbo"),
                        entry.get("ibo"),
                    ]
                    if texture_override is not None:
                        resources.append(texture_override)
                    model_item = MGLSceneItem(
                        name=path.name,
                        draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                        payload={
                            "vao": entry.get("vao"),
                            "mesh_entry": entry,
                            "texture": texture_override,
                            "color": self._mgl_mesh_color,
                            "material": material,
                            "owner": owner,
                            "path": path_key,
                            "copy_to_points": copy_to_points,
                            "music_effects": music_effects,
                            "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                            "fbx_bind_joints_only": bool(fbx_bind_joints_only),
                        },
                        resources=[res for res in resources if res is not None],
                        visible=model_visible,
                        order=10,
                        tag="scene-model",
                    )
                    total_indices += int(entry.get("count", 0))
                else:
                    if mesh_arrays is not None and mesh_arrays.submeshes:
                        original_override = self._mgl_texture_override
                        if texture_override is not None and not original_override:
                            self._mgl_texture_override = True
                        try:
                            entries, combined_uvs, _tex_paths, sub_count = self._mgl_build_submesh_entries(
                                mesh_arrays.submeshes
                            )
                        finally:
                            self._mgl_texture_override = original_override
                        if combined_uvs:
                            try:
                                self._mgl_scene_uvs_by_owner[owner] = np.concatenate(combined_uvs, axis=0)
                            except Exception:
                                pass
                        if texture_override is not None:
                            for sub in entries:
                                sub["texture"] = texture_override
                        proc_entry = None
                        try:
                            proc_entry = self._mgl_scene_proc_textures_by_owner.get(owner)
                        except Exception:
                            proc_entry = None
                        if proc_entry is not None:
                            proc_entry["subs"] = entries
                        resources = []
                        seen = set()
                        for sub in entries:
                            for res in (
                                sub.get("vao"),
                                sub.get("vbo"),
                                sub.get("nbo"),
                                sub.get("tbo"),
                                sub.get("cbo"),
                                sub.get("ibo"),
                                sub.get("texture"),
                            ):
                                if res is None:
                                    continue
                                rid = id(res)
                                if rid in seen:
                                    continue
                                seen.add(rid)
                                resources.append(res)
                        model_item = MGLSceneItem(
                            name=path.name,
                            draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                            payload={
                                "submeshes": entries,
                                "material": material,
                                "owner": owner,
                                "path": path_key,
                                "copy_to_points": copy_to_points,
                                "music_effects": music_effects,
                                "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                                "fbx_bind_joints_only": bool(fbx_bind_joints_only),
                            },
                            resources=resources,
                            visible=model_visible,
                            order=10,
                            tag="scene-model",
                        )
                        total_indices += int(sub_count)
                        if mesh_arrays.points is not None and mesh_arrays.points.size:
                            mins = mesh_arrays.points.min(axis=0)
                            maxs = mesh_arrays.points.max(axis=0)
                            try:
                                self._mgl_scene_bounds_by_owner[owner] = (mins.astype("f4"), maxs.astype("f4"))
                                self._mgl_scene_mesh_bounds_by_owner[owner] = (mins.astype("f4"), maxs.astype("f4"))
                            except Exception:
                                pass
                            if contribute_mesh_bounds and not gpu_copy_instances:
                                bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                                has_mesh_bounds = True
                        elif mesh_arrays.submeshes:
                            for sub in mesh_arrays.submeshes:
                                pts = getattr(sub, "points", None)
                                if pts is None or not getattr(pts, "size", 0):
                                    continue
                                mins = pts.min(axis=0)
                                maxs = pts.max(axis=0)
                                try:
                                    self._mgl_scene_bounds_by_owner[owner] = (mins.astype("f4"), maxs.astype("f4"))
                                    self._mgl_scene_mesh_bounds_by_owner[owner] = (mins.astype("f4"), maxs.astype("f4"))
                                except Exception:
                                    pass
                                if contribute_mesh_bounds and not gpu_copy_instances:
                                    bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                                    has_mesh_bounds = True
                        wire_sets = []
                        for sub in mesh_arrays.submeshes:
                            sub_points = getattr(sub, "points", None)
                            if sub_points is None or not getattr(sub_points, "size", 0):
                                continue
                            sub_lines = self._mgl_edge_vertices_from_mesh(sub_points)
                            if sub_lines is not None and getattr(sub_lines, "size", 0):
                                wire_sets.append(sub_lines)
                        if wire_sets:
                            try:
                                wire_points = np.concatenate(wire_sets, axis=0).astype("f4", copy=False)
                            except Exception:
                                wire_points = wire_sets[0]
                    else:
                        wire_points = self._mgl_edge_vertices_from_mesh(points)
                        entry = self._mgl_build_mesh_entry(points, normals, uvs)
                        if entry is None:
                            if material is not None:
                                self._mgl_material_log(
                                    "renderer.load.skip_mesh_entry_none",
                                    owner=owner,
                                    path=str(path),
                                    ext=ext,
                                    material=material,
                                )
                            continue
                        if uvs is not None and getattr(uvs, "size", 0):
                            try:
                                self._mgl_scene_uvs_by_owner[owner] = uvs.astype("f4").reshape(-1, 2)
                            except Exception:
                                pass
                        color = (
                            mesh_arrays.base_color
                            if mesh_arrays is not None and mesh_arrays.base_color is not None
                            else self._mgl_mesh_color
                        )
                        resources = [
                            entry.get("vao"),
                            entry.get("vbo"),
                            entry.get("nbo"),
                            entry.get("tbo"),
                            entry.get("cbo"),
                            entry.get("ibo"),
                        ]
                        if texture_override is not None:
                            resources.append(texture_override)
                        proc_entry = None
                        try:
                            proc_entry = self._mgl_scene_proc_textures_by_owner.get(owner)
                        except Exception:
                            proc_entry = None
                        if proc_entry is not None:
                            proc_entry["subs"] = [entry]
                        model_item = MGLSceneItem(
                            name=path.name,
                            draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                            payload={
                                "vao": entry.get("vao"),
                                "mesh_entry": entry,
                                "texture": texture_override,
                                "color": color,
                                "material": material,
                                "owner": owner,
                                "path": path_key,
                                "copy_to_points": copy_to_points,
                                "music_effects": music_effects,
                                "fbx_rig_context": fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                                "fbx_bind_joints_only": bool(fbx_bind_joints_only),
                            },
                            resources=[res for res in resources if res is not None],
                            visible=model_visible,
                            order=10,
                            tag="scene-model",
                        )
                        total_indices += int(entry.get("count", 0))

                if model_item is not None:
                    instance_count = self._mgl_setup_copy_to_points_instances(model_item)
                    if instance_count > 1:
                        try:
                            base_count = sum(
                                int(entry.get("count", 0) or 0)
                                for entry in self._mgl_mesh_entries_for_payload(model_item.payload or {})
                            )
                            total_indices += int(base_count) * int(instance_count - 1)
                        except Exception:
                            pass
                    scene.add(model_item)
                    if material is not None:
                        self._mgl_material_log(
                            "renderer.load.material_item",
                            owner=owner,
                            path=str(path),
                            ext=ext,
                            visible=bool(visible),
                            transparent=bool(self._mgl_material_is_transparent(material)),
                            transparency=float((material or {}).get("transparency", 0.0) or 0.0),
                            ior=float((material or {}).get("ior", 1.0) or 1.0),
                            tint_color=list((material or {}).get("tint_color", (1.0, 1.0, 1.0))),
                            fresnel_amount=float((material or {}).get("fresnel_amount", 0.0) or 0.0),
                            fresnel_color=list((material or {}).get("fresnel_color", (1.0, 1.0, 1.0))),
                            submeshes=int(len(model_item.payload.get("submeshes") or [])),
                            has_vao=bool(model_item.payload.get("vao") is not None),
                        )
                    if not first_mesh_path:
                        first_mesh_path = str(path)
                    if points is not None and getattr(points, "size", 0):
                        mins = points.min(axis=0)
                        maxs = points.max(axis=0)
                        try:
                            self._mgl_scene_bounds_by_owner[owner] = (mins.astype("f4"), maxs.astype("f4"))
                            self._mgl_scene_mesh_bounds_by_owner[owner] = (mins.astype("f4"), maxs.astype("f4"))
                        except Exception:
                            pass
                        if contribute_mesh_bounds and not gpu_copy_instances:
                            bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                            has_mesh_bounds = True
                    if gpu_copy_instances:
                        instance_bounds = self._mgl_copy_to_points_bounds(
                            copy_to_points if isinstance(copy_to_points, dict) else {}
                        )
                        if instance_bounds is not None:
                            try:
                                inst_min, inst_max = instance_bounds
                                self._mgl_scene_bounds_by_owner[owner] = (
                                    inst_min.astype("f4"),
                                    inst_max.astype("f4"),
                                )
                                self._mgl_scene_mesh_bounds_by_owner[owner] = (
                                    inst_min.astype("f4"),
                                    inst_max.astype("f4"),
                                )
                                if contribute_mesh_bounds:
                                    bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, inst_min, inst_max)
                                    has_mesh_bounds = True
                            except Exception:
                                pass
                    wire_item = None
                    if gpu_copy_instances:
                        wire_item = None
                    elif ext in (".obj", ".fbx"):
                        if ext == ".obj":
                            wire_item = self._mgl_add_obj_wire_item(
                                path,
                                bool(self._mgl_wireframe) and visible,
                                tag="scene-wire",
                                owner=owner,
                                path_key=path_key,
                            )
                        else:
                            wire_item = self._mgl_add_fbx_wire_item(
                                path,
                                bool(self._mgl_wireframe) and visible and (not bool(fbx_bind_joints_only)),
                                tag="scene-wire",
                                owner=owner,
                                path_key=path_key,
                                rig_context=fbx_rig_context if isinstance(fbx_rig_context, dict) else None,
                            )
                    elif wire_points is not None and getattr(wire_points, "size", 0):
                        wire_item = self._mgl_add_wire_item_from_points(
                            name=f"{path.name}-wire",
                            line_points=wire_points,
                            visible=bool(self._mgl_wireframe) and visible,
                            tag="scene-wire",
                            owner=owner,
                            path_key=path_key,
                        )
                    if wire_item is not None:
                        scene.add(wire_item)
                        model_item.payload["edge_wire"] = True
                    if ext == ".fbx":
                        self._mgl_fbx_joints_log(
                            "scene_asset wire "
                            + f"owner={owner} path={path} added={bool(wire_item is not None)} "
                            + f"wire_visible={bool(getattr(wire_item, 'visible', False)) if wire_item is not None else False} "
                            + f"bind_only={bool(fbx_bind_joints_only)}"
                        )
                    if ext == ".fbx" and path is not None and isinstance(fbx_rig_context, dict):
                        joint_overlays: List[MGLSceneItem] = []
                        capture_debug_enabled, animated_debug_enabled = self._mgl_fbx_joint_debug_modes(
                            fbx_rig_context
                        )
                        if capture_debug_enabled:
                            capture_item = self._mgl_add_fbx_joint_overlay_item(
                                path,
                                bool(visible),
                                pose_mode="capture",
                                owner=owner,
                                path_key=path_key,
                                rig_context=fbx_rig_context,
                            )
                            if capture_item is not None:
                                capture_item.order = 16
                                joint_overlays.append(capture_item)
                        if animated_debug_enabled:
                            animated_item = self._mgl_add_fbx_joint_overlay_item(
                                path,
                                bool(visible),
                                pose_mode="animated",
                                owner=owner,
                                path_key=path_key,
                                rig_context=fbx_rig_context,
                            )
                            if animated_item is not None:
                                animated_item.order = 16
                                joint_overlays.append(animated_item)
                        joint_bounds_min = None
                        joint_bounds_max = None
                        for overlay_item in joint_overlays:
                            scene.add(overlay_item)
                            try:
                                payload = dict(getattr(overlay_item, "payload", None) or {})
                                bmin = np.asarray(payload.get("bounds_min"), dtype="f4").reshape(-1)
                                bmax = np.asarray(payload.get("bounds_max"), dtype="f4").reshape(-1)
                                if bmin.size >= 3 and bmax.size >= 3:
                                    b0 = bmin[:3].astype("f4", copy=False)
                                    b1 = bmax[:3].astype("f4", copy=False)
                                    if joint_bounds_min is None or joint_bounds_max is None:
                                        joint_bounds_min, joint_bounds_max = b0.copy(), b1.copy()
                                    else:
                                        joint_bounds_min = np.minimum(joint_bounds_min, b0)
                                        joint_bounds_max = np.maximum(joint_bounds_max, b1)
                            except Exception:
                                pass
                        if (
                            bool(fbx_bind_joints_only)
                            and joint_bounds_min is not None
                            and joint_bounds_max is not None
                        ):
                            bounds_min, bounds_max = _merge_bounds(
                                bounds_min,
                                bounds_max,
                                joint_bounds_min,
                                joint_bounds_max,
                            )
                            has_mesh_bounds = True
                        self._mgl_fbx_joints_log(
                            "scene_asset overlays "
                            + f"owner={owner} path={path} count={int(len(joint_overlays))} "
                            + f"capture={bool(capture_debug_enabled)} "
                            + f"animated={bool(animated_debug_enabled)} "
                            + f"bind_only={bool(fbx_bind_joints_only)} "
                            + f"contrib_mesh_bounds={bool(contribute_mesh_bounds)}"
                        )
                    elif ext == ".fbx":
                        self._mgl_fbx_joints_log(
                            "scene_asset overlays skipped "
                            + f"owner={owner} path={path} reason=no_context"
                        )

            self._mgl_mesh_vertex_count = int(total_indices)
            if first_mesh_path:
                self._mgl_mesh_path = first_mesh_path

            preserve_camera = not frame

            # Apply scene xforms to currently loaded owners before any splat rebuild.
            try:
                applied_mesh = 0
                applied_splat = 0
                mesh_xforms = (
                    getattr(self, "_mgl_scene_xforms_by_owner", None)
                    if isinstance(getattr(self, "_mgl_scene_xforms_by_owner", None), dict)
                    else {}
                )
                mesh_owners = []
                if isinstance(mesh_owner_names, set) and mesh_owner_names:
                    for owner_name in sorted(mesh_owner_names, key=lambda value: str(value).strip().lower()):
                        owner_text = str(owner_name or "").strip()
                        if owner_text:
                            mesh_owners.append(owner_text)
                elif isinstance(prev_mesh_xforms, dict):
                    for owner_name in prev_mesh_xforms.keys():
                        owner_text = str(owner_name or "").strip()
                        if owner_text:
                            mesh_owners.append(owner_text)
                for owner_name in mesh_owners:
                    xf = self._mgl_casefold_get(mesh_xforms, owner_name)
                    if not isinstance(xf, dict) and isinstance(prev_mesh_xforms, dict):
                        xf = self._mgl_casefold_get(prev_mesh_xforms, owner_name)
                    try:
                        xf_pos = xf.get("pos") if isinstance(xf, dict) else None
                        xf_rot = xf.get("rot") if isinstance(xf, dict) else None
                        xf_scl = xf.get("scl") if isinstance(xf, dict) else None
                        self._mgl_set_scene_asset_xform(
                            owner_name,
                            pos=xf_pos,
                            rot=xf_rot,
                            scl=xf_scl,
                            apply_to_scene_models=True,
                            use_splat_xform=False,
                        )
                        applied_mesh += 1
                        try:
                            splat_map = getattr(self, "_mgl_scene_splats", None)
                            splat_owner = None
                            if isinstance(splat_map, dict):
                                if owner_name in splat_map:
                                    splat_owner = owner_name
                                else:
                                    owner_key = str(owner_name or "").strip().lower()
                                    for candidate in splat_map.keys():
                                        if str(candidate or "").strip().lower() == owner_key:
                                            splat_owner = str(candidate)
                                            break
                            if splat_owner:
                                self._mgl_set_scene_asset_xform(
                                    splat_owner,
                                    pos=xf_pos,
                                    rot=xf_rot,
                                    scl=xf_scl,
                                    apply_to_scene_models=False,
                                    use_splat_xform=True,
                                )
                                applied_splat += 1
                        except Exception:
                            pass
                    except Exception:
                        continue
                if isinstance(prev_splat_xforms, dict):
                    for owner, xf in prev_splat_xforms.items():
                        if not isinstance(xf, dict):
                            continue
                        try:
                            self._mgl_set_scene_asset_xform(
                                owner,
                                pos=xf.get("pos"),
                                rot=xf.get("rot"),
                                scl=xf.get("scl"),
                                apply_to_scene_models=False,
                                use_splat_xform=True,
                            )
                            applied_splat += 1
                        except Exception:
                            continue
                try:
                    self._mgl_log(
                        "scene: apply xforms mesh="
                        + str(applied_mesh)
                        + " splat="
                        + str(applied_splat)
                    )
                except Exception:
                    pass
            except Exception:
                pass

            try:
                if self._mgl_update_skinned_splat_proxies(force=True):
                    has_splats = True
            except Exception:
                pass

            if frame and has_mesh_bounds:
                try:
                    frame_min = None
                    frame_max = None
                    if isinstance(mesh_owner_names, set) and mesh_owner_names:
                        for owner_name in mesh_owner_names:
                            owner_text = str(owner_name or "").strip()
                            if not owner_text:
                                continue
                            owner_bounds = self.get_scene_owner_bounds(owner_text)
                            if (
                                not isinstance(owner_bounds, (tuple, list))
                                or len(owner_bounds) < 2
                            ):
                                continue
                            try:
                                bmin = np.asarray(owner_bounds[0], dtype="f4").reshape(-1)
                                bmax = np.asarray(owner_bounds[1], dtype="f4").reshape(-1)
                            except Exception:
                                continue
                            if bmin.size < 3 or bmax.size < 3:
                                continue
                            b0 = bmin[:3].astype("f4", copy=False)
                            b1 = bmax[:3].astype("f4", copy=False)
                            if frame_min is None or frame_max is None:
                                frame_min, frame_max = b0.copy(), b1.copy()
                            else:
                                frame_min = np.minimum(frame_min, b0)
                                frame_max = np.maximum(frame_max, b1)
                    if frame_min is None or frame_max is None:
                        if bounds_min is not None and bounds_max is not None:
                            frame_min, frame_max = bounds_min, bounds_max
                    if frame_min is not None and frame_max is not None:
                        pts = np.array([frame_min, frame_max], dtype="f4")
                        self._mgl_init_arcball(pts)
                        preserve_camera = True
                except Exception:
                    pass

            if has_splats:
                try:
                    self._mgl_log("splats: scene load complete, rebuilding splats")
                except Exception:
                    pass
                self._mgl_rebuild_scene_splats(preserve_camera=preserve_camera)
            else:
                try:
                    self._mgl_log("splats: scene load no splats, disable")
                except Exception:
                    pass
                self._mgl_disable_splats()
            try:
                rig_count = 0
                model_count = 0
                wire_count = 0
                for it in scene.items():
                    tag = str(getattr(it, "tag", "") or "")
                    if tag == "scene-rig-joints":
                        rig_count += 1
                    elif tag == "scene-model":
                        model_count += 1
                    elif tag == "scene-wire":
                        wire_count += 1
                self._mgl_fbx_joints_log(
                    "scene_load done "
                    + f"rig_items={rig_count} model_items={model_count} wire_items={wire_count} "
                    + f"has_mesh_bounds={bool(has_mesh_bounds)}"
                )
            except Exception:
                pass

        except Exception as exc:
            self._mgl_error = f"Scene load failed: {exc}"
        finally:
            if did_make_current:
                try:
                    self.doneCurrent()
                except Exception:
                    pass


    def pick_owner_at(self, px: int, py: int, viewport_w: int, viewport_h: int):
        """
        Hybrid pick:
        - Meshes: ray vs per-owner AABB (coarse, but stable)
        - Splats: nearest splat center to ray (no big-volume AABB dominance)

        Returns owner string or None.
        """
        if np is None:
            return None

        bounds = (
            getattr(self, "_mgl_scene_mesh_bounds_by_owner", None)
            or getattr(self, "_mgl_scene_bounds_by_owner", None)
            or {}
        )

        P = getattr(self, "_mgl_pick_proj", None)
        V = getattr(self, "_mgl_pick_view", None)
        M = getattr(self, "_mgl_pick_model", None)
        if P is None or V is None or M is None:
            return None

        try:
            invPV = np.linalg.inv((P @ V @ M).astype(np.float32))
        except Exception:
            return None

        # window coords -> active GL viewport NDC
        ndc_fn = getattr(self, "_mgl_screen_to_active_ndc", None)
        ndc = ndc_fn(px, py, viewport_w, viewport_h) if callable(ndc_fn) else None
        if ndc is None:
            return None
        x, y = ndc
        near = np.array([x, y, -1.0, 1.0], dtype=np.float32)
        far  = np.array([x, y,  1.0, 1.0], dtype=np.float32)

        p0 = invPV @ near
        p1 = invPV @ far
        if abs(p0[3]) < 1e-8 or abs(p1[3]) < 1e-8:
            return None
        p0 = p0[:3] / p0[3]
        p1 = p1[:3] / p1[3]

        ray_o = p0.astype(np.float32)
        ray_d = (p1 - p0).astype(np.float32)
        n = float(np.linalg.norm(ray_d))
        if n < 1e-8:
            return None
        ray_d /= n

        mesh_owners = None
        try:
            scene = getattr(self, "_mgl_scene", None)
            if scene is not None:
                mesh_owners = set()
                for item in scene.iter_by_tag("scene-model"):
                    payload = getattr(item, "payload", None) or {}
                    o = payload.get("owner")
                    if o:
                        mesh_owners.add(o)
        except Exception:
            mesh_owners = None

        def ray_aabb(o, d, bmin, bmax):
            # slabs method
            tmin = -1e30
            tmax =  1e30
            for k in range(3):
                if abs(d[k]) < 1e-8:
                    if o[k] < bmin[k] or o[k] > bmax[k]:
                        return None
                else:
                    inv = 1.0 / d[k]
                    t1 = (bmin[k] - o[k]) * inv
                    t2 = (bmax[k] - o[k]) * inv
                    if t1 > t2:
                        t1, t2 = t2, t1
                    tmin = max(tmin, float(t1))
                    tmax = min(tmax, float(t2))
                    if tmax < tmin:
                        return None
            if tmax < 0.0:
                return None
            return tmin if tmin >= 0.0 else tmax

        # --- splat picking: nearest splat center to ray (no big-volume dominance) ---
        best_splat_owner = None
        best_splat_t = 1e30

        splats_map = getattr(self, "_mgl_scene_splats_world", None) or getattr(self, "_mgl_scene_splats", None) or {}
        splat_bounds_map = getattr(self, "_mgl_scene_splat_bounds_by_owner", None)
        if splats_map:
            # tweak this if needed (world units)
            thresh = float(getattr(self, "_mgl_pick_splat_radius", 0.12))
            thresh2 = thresh * thresh

            for s_owner, arr in splats_map.items():
                try:
                    # coarse cull using bounds if available (prevents far-away hijacks)
                    try:
                        b = None
                        if isinstance(splat_bounds_map, dict):
                            b = splat_bounds_map.get(s_owner)
                        if b is None:
                            b = (getattr(self, "_mgl_scene_bounds_by_owner", {}) or {}).get(s_owner)
                        if b is not None:
                            bmin, bmax = b
                            bmin = np.array(bmin, dtype=np.float32) - thresh
                            bmax = np.array(bmax, dtype=np.float32) + thresh
                            if ray_aabb(ray_o, ray_d, bmin, bmax) is None:
                                continue
                    except Exception:
                        pass

                    pts = np.asarray(arr[:, :3] if getattr(arr, "ndim", 0) > 1 else arr, dtype=np.float32)
                    if pts.size == 0:
                        continue

                    v = pts - ray_o[None, :]
                    t = v @ ray_d  # (N,)
                    mask = t > 0.0
                    if not bool(np.any(mask)):
                        continue

                    tpos = t[mask]
                    pclose = ray_o[None, :] + tpos[:, None] * ray_d[None, :]
                    d = pts[mask] - pclose
                    d2 = np.einsum("ij,ij->i", d, d)

                    i = int(np.argmin(d2))
                    if float(d2[i]) <= thresh2:
                        tmin = float(tpos[i])
                        if tmin < best_splat_t:
                            best_splat_t = tmin
                            best_splat_owner = s_owner
                except Exception:
                    pass

        # --- mesh picking: transform ray into local space, then test AABB ---
        def T(tx, ty, tz):
            m = np.eye(4, dtype=np.float32)
            m[3, 0] = tx
            m[3, 1] = ty
            m[3, 2] = tz
            return m

        def S(sx, sy, sz):
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = sx
            m[1, 1] = sy
            m[2, 2] = sz
            return m

        def Rx(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[1, 1] = c
            m[1, 2] = s
            m[2, 1] = -s
            m[2, 2] = c
            return m

        def Ry(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = c
            m[0, 2] = -s
            m[2, 0] = s
            m[2, 2] = c
            return m

        def Rz(a):
            a = math.radians(a)
            c, s = math.cos(a), math.sin(a)
            m = np.eye(4, dtype=np.float32)
            m[0, 0] = c
            m[0, 1] = s
            m[1, 0] = -s
            m[1, 1] = c
            return m

        best_mesh_owner = None
        best_mesh_t = 1e30

        for owner, (bmin, bmax) in (bounds or {}).items():
            if owner in splats_map and (mesh_owners is None or owner not in mesh_owners):
                continue
            try:
                bmin = np.array(bmin, dtype=np.float32)
                bmax = np.array(bmax, dtype=np.float32)

                # Build model for this owner (matches render path)
                model = None
                try:
                    x = self._mgl_get_scene_asset_xform(owner)
                    px, py, pz = x.get("pos", (0.0, 0.0, 0.0))
                    rx, ry, rz = x.get("rot", (0.0, 0.0, 0.0))
                    sx, sy, sz = x.get("scl", (1.0, 1.0, 1.0))
                    cx, cy, cz = self._mgl_owner_pivot_local(owner, bmin, bmax)
                    rot_rx, rot_ry, rot_rz = -float(rx), -float(ry), -float(rz)
                    xform_space = str(getattr(self, "_mgl_xform_space", "world") or "world").lower()
                    if xform_space == "local":
                        model = (
                            T(-cx, -cy, -cz)
                            @ S(sx, sy, sz)
                            @ (Rx(rot_rx) @ Ry(rot_ry) @ Rz(rot_rz))
                            @ T(px, py, pz)
                        )
                    else:
                        model = (
                            T(-cx, -cy, -cz)
                            @ (Rx(rot_rx) @ Ry(rot_ry) @ Rz(rot_rz))
                            @ S(sx, sy, sz)
                            @ T(px, py, pz)
                        )
                except Exception:
                    model = None

                if model is not None:
                    try:
                        inv_model = np.linalg.inv(model)
                        o4 = np.array([ray_o[0], ray_o[1], ray_o[2], 1.0], dtype=np.float32)
                        d4 = np.array([ray_d[0], ray_d[1], ray_d[2], 0.0], dtype=np.float32)
                        # model is row-major; transform using row-vector convention
                        oL = o4 @ inv_model
                        dL = d4 @ inv_model
                        if abs(float(oL[3])) > 1e-8:
                            oL = oL[:3] / float(oL[3])
                        else:
                            oL = oL[:3]
                        dL = dL[:3]
                        nL = float(np.linalg.norm(dL))
                        if nL < 1e-8:
                            continue
                        dL /= nL
                        tL = ray_aabb(oL, dL, bmin, bmax)
                        if tL is None:
                            continue
                        hitL = oL + float(tL) * dL
                        hitW = np.array([hitL[0], hitL[1], hitL[2], 1.0], dtype=np.float32) @ model
                        if abs(float(hitW[3])) > 1e-8:
                            hitW = hitW[:3] / float(hitW[3])
                        else:
                            hitW = hitW[:3]
                        tW = float(np.dot(hitW - ray_o, ray_d))
                        if tW < 0.0:
                            continue
                        if tW < best_mesh_t:
                            best_mesh_t = tW
                            best_mesh_owner = owner
                        continue
                    except Exception:
                        pass

                # fallback: untransformed bounds
                t = ray_aabb(ray_o, ray_d, bmin, bmax)
                if t is not None and float(t) < best_mesh_t:
                    best_mesh_t = float(t)
                    best_mesh_owner = owner
            except Exception:
                pass

        if best_mesh_owner is None:
            try:
                self._mgl_last_pick_kind = "splat" if best_splat_owner is not None else None
                self._mgl_last_pick_owner = best_splat_owner
            except Exception:
                pass
            return best_splat_owner
        if best_splat_owner is None:
            try:
                self._mgl_last_pick_kind = "mesh"
                self._mgl_last_pick_owner = best_mesh_owner
            except Exception:
                pass
            return best_mesh_owner
        # When both hit, prefer mesh to avoid splat volumes hijacking selection.
        try:
            self._mgl_last_pick_kind = "mesh"
            self._mgl_last_pick_owner = best_mesh_owner
        except Exception:
            pass
        return best_mesh_owner


def pick_hit_at(self, px: int, py: int, viewport_w: int, viewport_h: int):
    """
    Like pick_owner_at, but returns (owner, hit_pos_world) where hit_pos_world is (x,y,z).
    Meshes: ray vs per-owner AABB intersection point.
    Splats: closest point on the ray to the chosen splat center.
    """
    if np is None:
        return None, None

    bounds = (
        getattr(self, "_mgl_scene_mesh_bounds_by_owner", None)
        or getattr(self, "_mgl_scene_bounds_by_owner", None)
        or {}
    )

    P = getattr(self, "_mgl_pick_proj", None)
    V = getattr(self, "_mgl_pick_view", None)
    M = getattr(self, "_mgl_pick_model", None)
    if P is None or V is None or M is None:
        return None, None

    try:
        invPV = np.linalg.inv((P @ V @ M).astype(np.float32))
    except Exception:
        return None, None

    # window coords -> active GL viewport NDC
    ndc_fn = getattr(self, "_mgl_screen_to_active_ndc", None)
    ndc = ndc_fn(px, py, viewport_w, viewport_h) if callable(ndc_fn) else None
    if ndc is None:
        return None, None
    x, y = ndc
    near = np.array([x, y, -1.0, 1.0], dtype=np.float32)
    far  = np.array([x, y,  1.0, 1.0], dtype=np.float32)

    p0 = invPV @ near
    p1 = invPV @ far
    if abs(p0[3]) < 1e-8 or abs(p1[3]) < 1e-8:
        return None, None
    p0 = p0[:3] / p0[3]
    p1 = p1[:3] / p1[3]

    ray_o = p0.astype(np.float32)
    ray_d = (p1 - p0).astype(np.float32)
    n = float(np.linalg.norm(ray_d))
    if n < 1e-8:
        return None, None
    ray_d /= n

    mesh_owners = None
    try:
        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            mesh_owners = set()
            for item in scene.iter_by_tag("scene-model"):
                payload = getattr(item, "payload", None) or {}
                o = payload.get("owner")
                if o:
                    mesh_owners.add(o)
    except Exception:
        mesh_owners = None

    def ray_aabb(o, d, bmin, bmax):
        tmin = -1e30
        tmax =  1e30
        for k in range(3):
            if abs(d[k]) < 1e-8:
                if o[k] < bmin[k] or o[k] > bmax[k]:
                    return None
            else:
                inv = 1.0 / d[k]
                t1 = (bmin[k] - o[k]) * inv
                t2 = (bmax[k] - o[k]) * inv
                if t1 > t2:
                    t1, t2 = t2, t1
                tmin = max(tmin, float(t1))
                tmax = min(tmax, float(t2))
                if tmax < tmin:
                    return None
        if tmax < 0.0:
            return None
        return tmin if tmin >= 0.0 else tmax

    # -----------------------------
    # splat picking
    # -----------------------------
    best_splat_owner = None
    best_splat_t = 1e30
    best_splat_hit = None

    splats_map = getattr(self, "_mgl_scene_splats_world", None) or getattr(self, "_mgl_scene_splats", None) or {}
    splat_bounds_map = getattr(self, "_mgl_scene_splat_bounds_by_owner", None)
    if splats_map:
        thresh = float(getattr(self, "_mgl_pick_splat_radius", 0.12))
        thresh2 = thresh * thresh

        for s_owner, arr in splats_map.items():
            try:
                # coarse cull using bounds if available (prevents far-away hijacks)
                splat_center = None
                try:
                    b = None
                    if isinstance(splat_bounds_map, dict):
                        b = splat_bounds_map.get(s_owner)
                    if b is None:
                        b = (getattr(self, "_mgl_scene_bounds_by_owner", {}) or {}).get(s_owner)
                    if b is not None:
                        bmin, bmax = b
                        bmin = np.array(bmin, dtype=np.float32) - thresh
                        bmax = np.array(bmax, dtype=np.float32) + thresh
                        try:
                            splat_center = (bmin + bmax) * 0.5
                        except Exception:
                            splat_center = None
                        if ray_aabb(ray_o, ray_d, bmin, bmax) is None:
                            continue
                except Exception:
                    pass

                pts = np.asarray(arr[:, :3] if getattr(arr, "ndim", 0) > 1 else arr, dtype=np.float32)
                if pts.size == 0:
                    continue

                v = pts - ray_o[None, :]
                t = v @ ray_d  # (N,)
                mask = t > 0.0
                if not bool(np.any(mask)):
                    continue

                tpos = t[mask]
                pclose = ray_o[None, :] + tpos[:, None] * ray_d[None, :]
                d = pts[mask] - pclose
                d2 = np.einsum("ij,ij->i", d, d)

                i = int(np.argmin(d2))
                if float(d2[i]) <= thresh2:
                    tmin = float(tpos[i])
                    if tmin < best_splat_t:
                        best_splat_t = tmin
                        best_splat_owner = s_owner
                        if splat_center is not None:
                            best_splat_hit = tuple(map(float, splat_center.tolist()))
                        else:
                            best_splat_hit = tuple(map(float, pclose[i].tolist()))
            except Exception:
                pass

    # -----------------------------
    # mesh picking (AABB)
    # -----------------------------

    def T(tx, ty, tz):
        m = np.eye(4, dtype=np.float32)
        m[3, 0] = tx
        m[3, 1] = ty
        m[3, 2] = tz
        return m

    def S(sx, sy, sz):
        m = np.eye(4, dtype=np.float32)
        m[0, 0] = sx
        m[1, 1] = sy
        m[2, 2] = sz
        return m

    def Rx(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        m = np.eye(4, dtype=np.float32)
        m[1, 1] = c
        m[1, 2] = s
        m[2, 1] = -s
        m[2, 2] = c
        return m

    def Ry(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        m = np.eye(4, dtype=np.float32)
        m[0, 0] = c
        m[0, 2] = -s
        m[2, 0] = s
        m[2, 2] = c
        return m

    def Rz(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        m = np.eye(4, dtype=np.float32)
        m[0, 0] = c
        m[0, 1] = s
        m[1, 0] = -s
        m[1, 1] = c
        return m

    best_mesh_owner = None
    best_mesh_t = 1e30
    best_mesh_hit = None

    for owner, (bmin, bmax) in (bounds or {}).items():
        if owner in splats_map and (mesh_owners is None or owner not in mesh_owners):
            continue
        try:
            bmin = np.array(bmin, dtype=np.float32)
            bmax = np.array(bmax, dtype=np.float32)

            model = None
            try:
                x = self._mgl_get_scene_asset_xform(owner)
                px, py, pz = x.get("pos", (0.0, 0.0, 0.0))
                rx, ry, rz = x.get("rot", (0.0, 0.0, 0.0))
                sx, sy, sz = x.get("scl", (1.0, 1.0, 1.0))
                cx, cy, cz = self._mgl_owner_pivot_local(owner, bmin, bmax)
                rot_rx, rot_ry, rot_rz = -float(rx), -float(ry), -float(rz)
                xform_space = str(getattr(self, "_mgl_xform_space", "world") or "world").lower()
                if xform_space == "local":
                    model = (
                        T(-cx, -cy, -cz)
                        @ S(sx, sy, sz)
                        @ (Rx(rot_rx) @ Ry(rot_ry) @ Rz(rot_rz))
                        @ T(px, py, pz)
                    )
                else:
                    model = (
                        T(-cx, -cy, -cz)
                        @ (Rx(rot_rx) @ Ry(rot_ry) @ Rz(rot_rz))
                        @ S(sx, sy, sz)
                        @ T(px, py, pz)
                    )
            except Exception:
                model = None

            if model is not None:
                try:
                    inv_model = np.linalg.inv(model)
                    o4 = np.array([ray_o[0], ray_o[1], ray_o[2], 1.0], dtype=np.float32)
                    d4 = np.array([ray_d[0], ray_d[1], ray_d[2], 0.0], dtype=np.float32)
                    # model is row-major; transform using row-vector convention
                    oL = o4 @ inv_model
                    dL = d4 @ inv_model
                    if abs(float(oL[3])) > 1e-8:
                        oL = oL[:3] / float(oL[3])
                    else:
                        oL = oL[:3]
                    dL = dL[:3]
                    nL = float(np.linalg.norm(dL))
                    if nL < 1e-8:
                        continue
                    dL /= nL
                    tL = ray_aabb(oL, dL, bmin, bmax)
                    if tL is None:
                        continue
                    hitL = oL + float(tL) * dL
                    hitW = np.array([hitL[0], hitL[1], hitL[2], 1.0], dtype=np.float32) @ model
                    if abs(float(hitW[3])) > 1e-8:
                        hitW = hitW[:3] / float(hitW[3])
                    else:
                        hitW = hitW[:3]
                    tW = float(np.dot(hitW - ray_o, ray_d))
                    if tW < 0.0:
                        continue
                    if tW < best_mesh_t:
                        best_mesh_t = tW
                        best_mesh_owner = owner
                        best_mesh_hit = tuple(map(float, hitW.tolist()))
                    continue
                except Exception:
                    pass

            t = ray_aabb(ray_o, ray_d, bmin, bmax)
            if t is not None and float(t) < best_mesh_t:
                best_mesh_t = float(t)
                best_mesh_owner = owner
                hp = ray_o + best_mesh_t * ray_d
                best_mesh_hit = tuple(map(float, hp.tolist()))
        except Exception:
            pass

    # -----------------------------
    # choose winner (closest along ray)
    # -----------------------------
    if best_mesh_owner is None and best_splat_owner is None:
        try:
            self._mgl_last_pick_kind = None
            self._mgl_last_pick_owner = None
        except Exception:
            pass
        return None, None
    if best_mesh_owner is None:
        try:
            self._mgl_last_pick_kind = "splat"
            self._mgl_last_pick_owner = best_splat_owner
        except Exception:
            pass
        return best_splat_owner, best_splat_hit
    if best_splat_owner is None:
        try:
            self._mgl_last_pick_kind = "mesh"
            self._mgl_last_pick_owner = best_mesh_owner
        except Exception:
            pass
        return best_mesh_owner, best_mesh_hit

    # When both hit, prefer mesh to avoid splat volumes hijacking selection.
    try:
        self._mgl_last_pick_kind = "mesh"
        self._mgl_last_pick_owner = best_mesh_owner
    except Exception:
        pass
    return best_mesh_owner, best_mesh_hit
