from __future__ import annotations

import math
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
from .gl_debug_geo import debug_cube_wire_vertices
from .gl_loaders import load_fbx_mesh_arrays_pyassimp
from .gl_loaders import load_fbx_edge_vertices
from .gl_loaders import load_gltf_mesh_arrays
from .gl_loaders import load_model
from .gl_loaders import load_obj_mesh_arrays
from .gl_scene import MGLSceneItem
from .gl_shaders import SHADERS
from .gl_types import SubMeshData

# OpenGL constants (avoid optional PyOpenGL dependency).
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100

_HAS_MGL = moderngl is not None and np is not None and Matrix44 is not None


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
            return None
        if line_points is None or line_points.size == 0:
            return None
        edge_count = int(line_points.shape[0] // 2)
        if edge_count <= 0:
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
            return None
        try:
            vbo = self._mgl_ctx.buffer(np.array(verts, dtype="f4").tobytes())
            vao_content = [(vbo, "3f 3f 3f 1f", "in_pos", "in_start", "in_end", "in_side")]
            vao = self._mgl_ctx.vertex_array(self._mgl_wire_prog, vao_content)
        except Exception:
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
        return item

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

    def _mgl_add_obj_wire_item(
        self,
        path: Path,
        visible: bool,
        tag: str = "model-wire",
        owner: Optional[str] = None,
        path_key: Optional[str] = None,
    ) -> Optional[MGLSceneItem]:
        try:
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
    ) -> Optional[MGLSceneItem]:
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
            "Mesh files (*.obj *.gltf *.glb *.fbx *.stl *.ply *.off *.om)",
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
        for tag in ("model", "model-wire", "scene-model", "scene-wire"):
            scene.remove_by_tag(tag)

    def _mgl_disable_splats(self) -> None:
        try:
            self._mgl_log("splats: disable")
        except Exception:
            pass
        self._mgl_pending_splats = None
        self._mgl_render_splats = False
        self._mgl_splat_count = 0
        self._mgl_splats15_cpu = None
        try:
            # keep dicts stable so picking/xforms don't fall back to mesh state
            if not isinstance(getattr(self, "_mgl_scene_splats_world", None), dict):
                self._mgl_scene_splats_world = {}
            else:
                self._mgl_scene_splats_world.clear()
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
        for item in scene.items():
            payload = item.payload or {}
            owner = payload.get("owner") or payload.get("node")
            path_key = payload.get("path")
            if owner == key or path_key == key:
                if item.tag == "scene-wire":
                    item.visible = bool(visible) and bool(getattr(self, "_mgl_wireframe", False))
                else:
                    item.visible = visible

    def _mgl_rename_scene_item_owner(self, old_name: str, new_name: str) -> None:
        scene = getattr(self, "_mgl_scene", None)
        if scene is None or not old_name or not new_name or old_name == new_name:
            return
        for item in scene.items():
            payload = item.payload or {}
            owner = payload.get("owner") or payload.get("node")
            if owner == old_name:
                payload["owner"] = new_name

    def _mgl_get_scene_asset_xform(self, owner: str):
        d = getattr(self, "_mgl_scene_xforms_by_owner", None)
        if not isinstance(d, dict):
            d = {}
            setattr(self, "_mgl_scene_xforms_by_owner", d)
        x = d.get(owner)
        if isinstance(x, dict):
            return x
        x = {"pos": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0), "scl": (1.0, 1.0, 1.0)}
        d[owner] = x
        return x

    def _mgl_get_scene_splat_xform(self, owner: str):
        d = getattr(self, "_mgl_scene_splat_xforms_by_owner", None)
        if not isinstance(d, dict):
            d = {}
            setattr(self, "_mgl_scene_splat_xforms_by_owner", d)
        x = d.get(owner)
        if isinstance(x, dict):
            return x
        x = {"pos": (0.0, 0.0, 0.0), "rot": (0.0, 0.0, 0.0), "scl": (1.0, 1.0, 1.0)}
        d[owner] = x
        return x

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

        x = self._mgl_get_scene_splat_xform(owner) if use_splat_xform else self._mgl_get_scene_asset_xform(owner)
        if pos is not None:
            x["pos"] = tuple(float(v) for v in pos)
        if rot is not None:
            x["rot"] = tuple(float(v) for v in rot)
        if scl is not None:
            x["scl"] = tuple(float(v) for v in scl)

        # pivot around asset bounds center if we have it
        cx = cy = cz = 0.0
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

            b = (bounds_map or {}).get(owner) if isinstance(bounds_map, dict) else None
            if b is not None:
                bmin, bmax = b
                c = (bmin + bmax) * 0.5
                cx, cy, cz = float(c[0]), float(c[1]), float(c[2])
        except Exception:
            pass

        px, py, pz = x["pos"]
        rx, ry, rz = x["rot"]  # degrees
        sx, sy, sz = x["scl"]

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
                for tag in ("scene-model", "scene-wire"):
                    for item in scene.iter_by_tag(tag):
                        payload = getattr(item, "payload", None) or {}
                        if payload.get("owner") != owner:
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

            # pivot = LOCAL bounds center if available, else current center
            try:
                b = (bounds_local or {}).get(owner)
                if b is not None:
                    bmin, bmax = b
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
                out[:, 8] *= sx
                out[:, 9] *= sy
                out[:, 10] *= sz

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
            try:
                splats_world[owner] = a15[:, :3].astype(np.float32, copy=True)
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
            self._mgl_scene_splats_world = splats_world
        except Exception:
            pass

        combined = arrays15[0] if len(arrays15) == 1 else np.concatenate(arrays15, axis=0)
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

                vbo = getattr(self, "_mgl_splatq_vbo", None)
                if vbo is not None:
                    try:
                        vbo_size = int(getattr(vbo, "size", 0) or 0)
                    except Exception:
                        vbo_size = 0

                    if vbo_size == int(splats15.nbytes):
                        # keep CPU copy (sorting path relies on this)
                        self._mgl_splat_count = int(splats15.shape[0])
                        self._mgl_splats15_cpu = splats15

                        vbo.write(splats15.tobytes())
                        did_in_place = True
        except Exception:
            did_in_place = False

        # Fallback: count changed or buffer missing, do the full rebuild
        if not did_in_place:
            self.set_splats(combined)

        try:
            self._mgl_splats_need_rebuild = False
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
        submeshes = payload.get("submeshes")
        vao = payload.get("vao")
        if not submeshes and vao is None:
            return
        edge_wire = bool(payload.get("edge_wire"))
        try:
            payload = item.payload or {}

            mvp_to_use = mvp
            model = payload.get("model")
            if model is not None and Matrix44 is not None:
                try:
                    if isinstance(model, Matrix44):
                        mvp_to_use = mvp * model
                    else:
                        # model may be a numpy 4x4; Matrix44 can build from it
                        mvp_to_use = mvp * Matrix44(model, dtype="f4")
                except Exception:
                    mvp_to_use = mvp

            self._mgl_prog["Mvp"].write(mvp_to_use.astype("f4").tobytes())

        except Exception:
            pass
        manual_texture = self._mgl_texture if self._mgl_texture_override else None
        wire_overlay = bool(self._mgl_wireframe and not edge_wire and (submeshes or vao is not None))
        if wire_overlay:
            try:
                self._mgl_ctx.polygon_offset = (1.0, 1.0)
            except Exception:
                pass

        if submeshes:
            for sub in submeshes:
                color = sub.get("color") or self._mgl_mesh_color
                tex = manual_texture or sub.get("texture")
                use_texture = tex is not None
                try:
                    self._mgl_prog["UseTexture"].value = 1 if use_texture else 0
                    self._mgl_prog["UseLighting"].value = 1
                    self._mgl_prog["LightIntensity"].value = float(self._mgl_light_intensity)
                    self._mgl_prog["Color"].value = color
                except Exception:
                    pass
                if use_texture:
                    try:
                        tex.use(location=0)
                    except Exception:
                        pass
                sub_vao = sub.get("vao")
                if sub_vao is not None:
                    sub_vao.render()
            if wire_overlay:
                try:
                    self._mgl_ctx.polygon_offset = (0.0, 0.0)
                except Exception:
                    pass
                try:
                    self._mgl_ctx.line_width = float(self._mgl_wire_line_width)
                except Exception:
                    pass
                self._mgl_ctx.wireframe = True
                try:
                    self._mgl_prog["UseTexture"].value = 0
                    self._mgl_prog["UseLighting"].value = 0
                    self._mgl_prog["Color"].value = self._mgl_wire_color
                except Exception:
                    pass
                for sub in submeshes:
                    sub_vao = sub.get("vao")
                    if sub_vao is not None:
                        sub_vao.render()
                self._mgl_ctx.wireframe = False
                try:
                    self._mgl_ctx.line_width = 1.0
                except Exception:
                    pass
        else:
            color = payload.get("color") or self._mgl_mesh_color
            tex = manual_texture or payload.get("texture")
            use_texture = tex is not None
            try:
                self._mgl_prog["UseTexture"].value = 1 if use_texture else 0
                self._mgl_prog["UseLighting"].value = 1
                self._mgl_prog["LightIntensity"].value = float(self._mgl_light_intensity)
                self._mgl_prog["Color"].value = color
            except Exception:
                pass
            if use_texture:
                try:
                    tex.use(location=0)
                except Exception:
                    pass
            if vao is not None:
                vao.render()
            if wire_overlay and vao is not None:
                try:
                    self._mgl_ctx.polygon_offset = (0.0, 0.0)
                except Exception:
                    pass
                try:
                    self._mgl_ctx.line_width = float(self._mgl_wire_line_width)
                except Exception:
                    pass
                self._mgl_ctx.wireframe = True
                try:
                    self._mgl_prog["UseTexture"].value = 0
                    self._mgl_prog["UseLighting"].value = 0
                    self._mgl_prog["Color"].value = self._mgl_wire_color
                except Exception:
                    pass
                vao.render()
                self._mgl_ctx.wireframe = False
                try:
                    self._mgl_ctx.line_width = 1.0
                except Exception:
                    pass

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
                self._mgl_grid_prog["Color"].value = (0.55, 0.55, 0.55, float(getattr(self, "_mgl_grid_alpha", 0.10)))

            except Exception:
                color = (0.8, 0.8, 0.8, float(self._mgl_grid_alpha))
        try:
            self._mgl_grid_prog["Mvp"].write(mvp.astype("f4").tobytes())
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
        vao = payload.get("vao")
        if vao is None:
            return
        color = payload.get("color") or self._mgl_wire_color
        try:
            mvp_to_use = mvp
            model = payload.get("model")
            if model is not None and Matrix44 is not None:
                try:
                    if isinstance(model, Matrix44):
                        mvp_to_use = mvp * model
                    else:
                        mvp_to_use = mvp * Matrix44(model, dtype="f4")
                except Exception:
                    mvp_to_use = mvp
            self._mgl_wire_prog["Mvp"].write(mvp_to_use.astype("f4").tobytes())
            self._mgl_wire_prog["Color"].value = color
            self._mgl_wire_prog["Viewport"].value = (float(max(1, self.width())), float(max(1, self.height())))
            self._mgl_wire_prog["LineWidth"].value = float(
                getattr(self, "_mgl_wire_edge_width", getattr(self, "_mgl_wire_line_width", 1.0))
            )
        except Exception:
            pass
        try:
            mode = payload.get("mode")
            if mode is None:
                vao.render()
            else:
                vao.render(mode)
        except Exception as exc:
            self._mgl_error = f"Scene wire draw failed: {exc}"

    def _mgl_build_mesh_entry(
        self,
        points: NDArray,
        normals: NDArray,
        uvs: Optional[NDArray] = None,
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
        if indices is None:
            indices = np.arange(points.shape[0], dtype="u4")
        else:
            indices = np.asarray(indices, dtype="u4").ravel()
        index_buffer = self._mgl_ctx.buffer(indices.tobytes())
        pos_buf = self._mgl_ctx.buffer(points.tobytes())
        norm_buf = self._mgl_ctx.buffer(normals.tobytes())
        uv_buf = self._mgl_ctx.buffer(uvs.tobytes())
        vao_content = [
            (pos_buf, "3f", "in_position"),
            (norm_buf, "3f", "in_normal"),
            (uv_buf, "2f", "in_uv"),
        ]
        vao = self._mgl_ctx.vertex_array(self._mgl_prog, vao_content, index_buffer, 4)
        return {
            "vao": vao,
            "vbo": pos_buf,
            "nbo": norm_buf,
            "tbo": uv_buf,
            "ibo": index_buffer,
            "count": int(indices.size),
            "uvs": uvs,
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
            vao_content = [
                (vbo, "3f", "in_position"),
                (nbo, "3f", "in_normal"),
                (tbo, "2f", "in_uv"),
            ]
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
                    "ibo": ibo,
                    "texture": texture,
                    "color": color,
                    "count": int(indices.size),
                }
            )
            total_indices += int(indices.size)
            combined_uvs.append(uvs)
        return entries, combined_uvs, texture_paths, total_indices

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

    def _mgl_update_procedural_textures(self, step: float, frame_id: int) -> None:
        if self._mgl_ctx is None or step <= 0.0:
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

    def _mgl_bind_default_fbo(self) -> None:
        try:
            if self._gl is not None and hasattr(self, "defaultFramebufferObject"):
                self._gl.glBindFramebuffer(0x8D40, int(self.defaultFramebufferObject()))  # GL_FRAMEBUFFER
        except Exception:
            pass

    def _paint_mgl(self) -> None:
        if getattr(self, "_render_paused", False):
            return
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
        step = 1.0 / max(self._fps, 1.0)
        try:
            self._mgl_update_procedural_textures(step, frame_id)
        except Exception:
            pass

        dbg = bool(getattr(self, "_mgl_debug", False))
        self._dbgprint(dbg, "[MGL] ENTER _paint_mgl", flush=True)

        if not _HAS_MGL or self._mgl_ctx is None:
            try:
                c = self._viewport_bg
                self._gl.glClearColor(c.redF(), c.greenF(), c.blueF(), 1.0)
            except Exception:
                pass
            return
        try:
            self._mgl_bind_default_fbo()
            # QOpenGLWidget already has the correct default framebuffer bound.
            # Avoid Framebuffer.clear() because it may bind/use() internally and can hard-crash some drivers.
            self._dbgprint(dbg, "[MGL] set viewport", flush=True)
            self._mgl_ctx.viewport = (0, 0, max(2, self.width()), max(2, self.height()))
            self._dbgprint(dbg, "[MGL] after viewport assign", flush=True)

            col = self._mgl_bg_color or (0.15, 0.15, 0.15, 1.0)
            if len(col) >= 4:
                r, g, b, a = col[:4]
            else:
                r, g, b = col[:3]
                a = 1.0

            try:
                if self._gl is not None:
                    w = max(2, self.width())
                    h = max(2, self.height())
                    self._dbgprint(dbg, "[MGL] before glViewport", flush=True)
                    self._gl.glViewport(0, 0, w, h)
                    self._dbgprint(dbg, "[MGL] after glViewport", flush=True)

                    self._dbgprint(dbg, "[MGL] before glClearColor", flush=True)
                    self._gl.glClearColor(r, g, b, a)
                    self._dbgprint(dbg, "[MGL] after glClearColor", flush=True)

                    # bind Qt's default FBO (snapshot/grab can change the bound framebuffer)
                    try:
                        fbo = int(self.defaultFramebufferObject())
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
                            fbo = int(self.defaultFramebufferObject())
                            self._gl.glBindFramebuffer(0x8D40, fbo)  # GL_FRAMEBUFFER
                        except Exception:
                            pass

                        self._gl.glClearColor(r, g, b, a)
                        try:
                            self._gl.glClearDepth(1.0)
                        except Exception:
                            pass
                        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
                except Exception:
                    pass

            self._dbgprint(dbg, "[MGL] after raw gl clear block", flush=True)

            # Ensure Qt's default framebuffer is bound (snapshot/grab can change FBO binding)
            try:
                if self._gl is not None and hasattr(self, "defaultFramebufferObject"):
                    fbo = int(self.defaultFramebufferObject())
                    self._gl.glBindFramebuffer(0x8D40, fbo)  # GL_FRAMEBUFFER
            except Exception:
                pass

        except Exception as exc:
            import traceback
            self._mgl_error = f"ModernGL framebuffer error: {exc}"
            self._dbgprint(dbg, "[MGL] framebuffer exception:", exc, flush=True)
            traceback.print_exc()
            return

        flags = moderngl.BLEND | moderngl.DEPTH_TEST
        if self._mgl_cull_enabled:
            flags |= moderngl.CULL_FACE
        self._dbgprint(dbg, "[MGL] before mgl enable", flush=True)
        self._mgl_ctx.enable(flags)
        self._dbgprint(dbg, "[MGL] after mgl enable", flush=True)

        self._mgl_ctx.wireframe = False

        self._dbgprint(dbg, "[MGL] after wireframe", flush=True)
        if self._mgl_prog is None or self._mgl_grid_prog is None:
            return
        self._dbgprint(dbg, "[MGL] prog ok", flush=True)

        pending_grid = getattr(self, "_mgl_grid_model_pending_path", None)
        if pending_grid is not None:
            self._mgl_grid_model_pending_path = None

            # Only load the legacy grid-model if it is explicitly enabled.
            if bool(getattr(self, "_mgl_grid_model_visible", False)):
                self._mgl_load_grid_model(Path(pending_grid), in_paint=True)


        aspect = self.width() / max(1.0, self.height())
        try:
            zoom = float(getattr(self, "_mgl_camera_zoom", 1.0))
        except Exception:
            zoom = 1.0
        near = max(0.0005, min(0.05, zoom * 0.01))
        try:
            far = float(getattr(self, "_mgl_clip_far", 1000.0))
        except Exception:
            far = 1000.0
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

        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            scene.draw(self, mvp)

        # --- GRID (draw BEFORE splats so splats layer on top) ---
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
                # Save state we touch
                try:
                    _prev_depth_mask = bool(getattr(self._mgl_ctx, "depth_mask", True))
                except Exception:
                    _prev_depth_mask = True
                try:
                    _prev_lw = float(getattr(self._mgl_ctx, "line_width", 1.0))
                except Exception:
                    _prev_lw = 1.0

                # Draw grid with depth test so it stays behind meshes/splats.
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

                # Use the configured grid alpha (default is high for visibility).
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
                    fade_start = render_size * max(0.0, min(1.0, fade_start_frac))
                    fade_end = render_size * max(0.0, min(1.0, fade_end_frac))
                    max_fade = render_size * 0.98
                    if not grid_fx:
                        if max_fade > 0.0:
                            ratio = 0.0
                            if fade_end_frac > 1e-6:
                                ratio = max(0.0, fade_start_frac / fade_end_frac)
                            fade_end = max_fade
                            fade_start = max(0.0, fade_end * ratio)
                    else:
                        # Tie fade radius to camera distance with smooth exponential scaling.
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
                    if fade_end > max_fade:
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
                    # Block 0: lines parallel to Z at constant X (skip X=0 when it falls inside this block)
                    if skip_x is None:
                        self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=block_verts, first=0)
                    else:
                        before = skip_x * 2
                        after = (steps - skip_x - 1) * 2
                        if before > 0:
                            self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=before, first=0)
                        if after > 0:
                            self._mgl_grid_vao.render(mode=moderngl.LINES, vertices=after, first=(skip_x + 1) * 2)
                    # Block 1: lines parallel to X at constant Z (skip Z=0 when it falls inside this block)
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

                    # Draw the 2 center axes once (thicker) so origin reads as "+"
                    try:
                        _center_prev_lw = float(getattr(self._mgl_ctx, "line_width", 1.0))
                    except Exception:
                        _center_prev_lw = 1.0
                    try:
                        self._mgl_ctx.line_width = 2.0
                    except Exception:
                        pass
                    try:
                        self._mgl_grid_prog["Color"].value = (0.24, 0.24, 0.24, 0.12)
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

                # Restore
                try:
                    self._mgl_ctx.line_width = _prev_lw
                except Exception:
                    pass
                try:
                    self._mgl_ctx.depth_mask = _prev_depth_mask
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


        # --- SPLATS (instanced-quad only) ---
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
                do_sort = (self._mgl_splat_sort_tick % 10) == 0  # sort every 10th frame

                dbg = bool(getattr(self, "_mgl_debug", False))

                # SORT (only sometimes)
                try:
                    if do_sort and np is not None:
                        cpu = getattr(self, "_mgl_splats15_cpu", None)
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
                except Exception as exc:
                    if dbg:
                        print("[SPLATQ] sort error:", exc, flush=True)

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

        except Exception as exc:
            import traceback
            if dbg:
                print("[SPLATQ] PAINT CRASH:", exc)
            traceback.print_exc()

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

        # scene xforms (mesh + splat) for snapshot persistence
        try:
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

        if getattr(self, "_mgl_pending_splats", None) is None:
            self._mgl_pending_cam_state = None
            self._mgl_apply_camera_state(state)
            return

        self._mgl_pending_cam_state = dict(state)

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
            xf_state = state.get("scene_xforms", None)
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
        self._mgl_light_intensity = intensity
        if getattr(self, "_mgl_light_label", None) is not None:
            self._mgl_light_label.setText(f"Light {intensity:.2f}x")
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
            self._mgl_ctx.enable(moderngl.BLEND | moderngl.DEPTH_TEST)
            self._mgl_ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)

            # mesh shaders
            mesh_vertex = SHADERS["mesh_vertex"]
            mesh_fragment = SHADERS["mesh_fragment"]
            grid_vertex = SHADERS["grid_vertex"]
            grid_fragment = SHADERS["grid_fragment"]
            wire_vertex = SHADERS["wire_vertex"]
            wire_fragment = SHADERS["wire_fragment"]

            self._mgl_prog = self._mgl_ctx.program(vertex_shader=mesh_vertex, fragment_shader=mesh_fragment)
            self._mgl_grid_prog = self._mgl_ctx.program(vertex_shader=grid_vertex, fragment_shader=grid_fragment)
            self._mgl_wire_prog = self._mgl_ctx.program(vertex_shader=wire_vertex, fragment_shader=wire_fragment)
            self._mgl_prog["Light"].value = (1.0, 1.0, 1.0)
            self._mgl_prog["Color"].value = self._mgl_mesh_color
            try:
                self._mgl_prog["Texture"].value = 0
                self._mgl_prog["UseTexture"].value = 0
                self._mgl_prog["LightIntensity"].value = float(self._mgl_light_intensity)
                self._mgl_prog["UseLighting"].value = 1
            except Exception:
                pass
            self._mgl_grid_prog["Color"].value = (0.8, 0.8, 0.8, self._mgl_grid_alpha)
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
            self._mgl_update_grid()
            self._mgl_error = ""

            # splat shaders
            splat_vertex = SHADERS["splat_vertex"]
            splat_fragment = SHADERS["splat_fragment"]
            splatq_vertex = SHADERS["splatq_vertex"]
            splatq_fragment = SHADERS["splatq_fragment"]

            self._mgl_splat_prog = self._mgl_ctx.program(vertex_shader=splat_vertex, fragment_shader=splat_fragment)
            self._mgl_splatq_prog = self._mgl_ctx.program(vertex_shader=splatq_vertex, fragment_shader=splatq_fragment)

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

    def set_splats(self, splats_np) -> None:
        """Queue splat instance data for GL-thread upload.
        Accepts (N,8), (N,10), (N,14), or (N,15) float32 arrays.

        (N,8):  [x,y,z, r,g,b,a, radius]
        (N,10): [x,y,z, r,g,b,a, radius, sx, sy]
        (N,14): [x,y,z, r,g,b,a, radius, sx, sy, qx, qy, qz, qw]
        (N,15): [x,y,z, r,g,b,a, radius, sx, sy, sz, qx, qy, qz, qw]
        """
        if np is None:
            self._mgl_pending_splats = None
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
        self._mgl_pending_splats = None
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

        if self._mgl_splatq_vbo is not None:
            try:
                self._mgl_splatq_vbo.release()
            except Exception:
                pass
            self._mgl_splatq_vbo = None

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

                # Keep CPU copy so we can sort per-frame (10k is fine)
                self._mgl_splats15_cpu = splats15

                # release previous GPU objects before replacing them
                try:
                    if self._mgl_splatq_vao is not None and hasattr(self._mgl_splatq_vao, "release"):
                        self._mgl_splatq_vao.release()
                except Exception:
                    pass
                try:
                    if self._mgl_splatq_vbo is not None and hasattr(self._mgl_splatq_vbo, "release"):
                        self._mgl_splatq_vbo.release()
                except Exception:
                    pass
                self._mgl_splatq_vao = None
                self._mgl_splatq_vbo = None

                self._mgl_splatq_vbo = self._mgl_ctx.buffer(splats15.tobytes())
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
                        ],
                    )
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
        entry = self._mgl_build_mesh_entry(points, normals, None, indices)
        if entry is None:
            return
        self._mgl_vao = entry.get("vao")
        self._mgl_mesh_vbos = [entry.get("vbo"), entry.get("nbo"), entry.get("tbo")]
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
                entry.get("ibo"),
            ]
            item = MGLSceneItem(
                name="mesh",
                draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                payload={"vao": entry.get("vao"), "texture": None, "color": self._mgl_mesh_color},
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
            for key in ("vbo", "nbo", "tbo", "ibo"):
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
        self._mgl_mesh_vbos = [entry.get("vbo"), entry.get("nbo"), entry.get("tbo")]
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
                entry.get("ibo"),
            ]
            item = MGLSceneItem(
                name="mesh",
                draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                payload={"vao": entry.get("vao"), "texture": None, "color": self._mgl_mesh_color},
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
                        sub.get("ibo"),
                        sub.get("texture"),
                    ]
                )
            item = MGLSceneItem(
                name="mesh",
                draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                payload={"submeshes": entries},
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
        if self._mgl_texture is not None:
            try:
                self._mgl_texture.release()
            except Exception:
                pass
        self._mgl_texture = None
        self._mgl_texture_path = ""
        self._mgl_texture_paths = []
        self._mgl_texture_override = False
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
        if mesh is None:
            if path.suffix.lower() == ".fbx":
                try:
                    mesh_arrays = load_fbx_mesh_arrays_pyassimp(path)
                    points = mesh_arrays.points
                    normals = mesh_arrays.normals
                    uvs = mesh_arrays.uvs
                except Exception as exc:
                    self._mgl_error = f"FBX load failed: {exc}"
                    return
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
            if mesh is not None:
                mesh.update_normals()
                points = np.array(mesh.points(), dtype="f4")
                normals = np.array(mesh.vertex_normals(), dtype="f4")
                indices = np.array(mesh.face_vertex_indices(), dtype="u4").ravel()
                entry = self._mgl_build_mesh_entry(points, normals, None, indices)
                if entry is None:
                    self._mgl_error = "Mesh upload failed"
                    return
                self._mgl_vao = entry.get("vao")
                self._mgl_mesh_vbos = [entry.get("vbo"), entry.get("nbo"), entry.get("tbo")]
                self._mgl_index_buffer = entry.get("ibo")
                resources = [
                    entry.get("vao"),
                    entry.get("vbo"),
                    entry.get("nbo"),
                    entry.get("tbo"),
                    entry.get("ibo"),
                ]
                item = MGLSceneItem(
                    name=path.name,
                    draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                    payload={"vao": entry.get("vao"), "texture": None, "color": self._mgl_mesh_color},
                    resources=[res for res in resources if res is not None],
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
                                sub.get("ibo"),
                                sub.get("texture"),
                            ]
                        )
                    item = MGLSceneItem(
                        name=path.name,
                        draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                        payload={"submeshes": entries},
                        resources=[res for res in resources if res is not None],
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
                    self._mgl_mesh_vbos = [entry.get("vbo"), entry.get("nbo"), entry.get("tbo")]
                    self._mgl_index_buffer = entry.get("ibo")
                    resources = [
                        entry.get("vao"),
                        entry.get("vbo"),
                        entry.get("nbo"),
                        entry.get("tbo"),
                        entry.get("ibo"),
                    ]
                    item = MGLSceneItem(
                        name=path.name,
                        draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                        payload={"vao": entry.get("vao"), "texture": None, "color": self._mgl_mesh_color},
                        resources=[res for res in resources if res is not None],
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
                    wire_item = self._mgl_add_fbx_wire_item(path, bool(self._mgl_wireframe))
                if wire_item is not None:
                    scene.add(wire_item)
                    if model_item is not None:
                        model_item.payload["edge_wire"] = True
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
        self._mgl_proc_provider = None
        self._mgl_proc_label = ""
        self._mgl_proc_rev = -1
        # prevent texture leaking from previous "Texture..." or textured Import views
        self._mgl_texture_override = False
        self._mgl_texture = None
        self._mgl_texture_path = ""
        self._mgl_texture_paths = []

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
            self._mgl_scene_splats_bounds_local = {}
            self._mgl_scene_splat_bounds_by_owner = {}
            self._mgl_scene_mesh_bounds_by_owner = {}
            # Preserve xforms loaded from workflow
            self._mgl_scene_xforms_by_owner = prev_mesh_xforms
            self._mgl_scene_splat_xforms_by_owner = prev_splat_xforms
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
                
                path_str = str(asset.get("path", "") or "").strip()
                if not path_str:
                    continue
                path = Path(path_str)
                if not path.exists():
                    continue
                ext = path.suffix.lower()
                owner = str(asset.get("node") or "").strip()
                if not owner:
                    owner = path.name
                path_key = str(path)
                visibility_map = getattr(self, "_mgl_scene_visibility", {}) or {}
                visible = bool(visibility_map.get(owner, True))

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
                            self._mgl_error = f"FBX load failed: {exc}"
                            continue
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
                if mesh is not None:
                    mesh.update_normals()
                    points = np.array(mesh.points(), dtype="f4")
                    normals = np.array(mesh.vertex_normals(), dtype="f4")
                    indices = np.array(mesh.face_vertex_indices(), dtype="u4").ravel()
                    entry = self._mgl_build_mesh_entry(points, normals, None, indices)
                    if entry is None:
                        continue
                    resources = [
                        entry.get("vao"),
                        entry.get("vbo"),
                        entry.get("nbo"),
                        entry.get("tbo"),
                        entry.get("ibo"),
                    ]
                    if texture_override is not None:
                        resources.append(texture_override)
                    model_item = MGLSceneItem(
                        name=path.name,
                        draw_fn=MGLRendererMixin._mgl_draw_scene_mesh,
                        payload={
                            "vao": entry.get("vao"),
                            "texture": texture_override,
                            "color": self._mgl_mesh_color,
                            "owner": owner,
                            "path": path_key,
                        },
                        resources=[res for res in resources if res is not None],
                        visible=visible,
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
                            payload={"submeshes": entries, "owner": owner, "path": path_key},
                            resources=resources,
                            visible=visible,
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
                                bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                                has_mesh_bounds = True
                    else:
                        entry = self._mgl_build_mesh_entry(points, normals, uvs)
                        if entry is None:
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
                                "texture": texture_override,
                                "color": color,
                                "owner": owner,
                                "path": path_key,
                            },
                            resources=[res for res in resources if res is not None],
                            visible=visible,
                            order=10,
                            tag="scene-model",
                        )
                        total_indices += int(entry.get("count", 0))

                if model_item is not None:
                    scene.add(model_item)
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
                        bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                        has_mesh_bounds = True
                    if ext in (".obj", ".fbx"):
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
                                bool(self._mgl_wireframe) and visible,
                                tag="scene-wire",
                                owner=owner,
                                path_key=path_key,
                            )
                        if wire_item is not None:
                            scene.add(wire_item)
                            model_item.payload["edge_wire"] = True

            self._mgl_mesh_vertex_count = int(total_indices)
            if first_mesh_path:
                self._mgl_mesh_path = first_mesh_path

            preserve_camera = not frame
            if frame and has_mesh_bounds and bounds_min is not None and bounds_max is not None:
                try:
                    pts = np.array([bounds_min, bounds_max], dtype="f4")
                    self._mgl_init_arcball(pts)
                    preserve_camera = True
                except Exception:
                    pass

            # Apply saved xforms to scene items before any splat rebuild.
            try:
                applied_mesh = 0
                applied_splat = 0
                if isinstance(prev_mesh_xforms, dict):
                    for owner, xf in prev_mesh_xforms.items():
                        if not isinstance(xf, dict):
                            continue
                        self._mgl_set_scene_asset_xform(
                            owner,
                            pos=xf.get("pos"),
                            rot=xf.get("rot"),
                            scl=xf.get("scl"),
                            apply_to_scene_models=True,
                            use_splat_xform=False,
                        )
                        applied_mesh += 1
                if isinstance(prev_splat_xforms, dict):
                    for owner, xf in prev_splat_xforms.items():
                        if not isinstance(xf, dict):
                            continue
                        self._mgl_set_scene_asset_xform(
                            owner,
                            pos=xf.get("pos"),
                            rot=xf.get("rot"),
                            scl=xf.get("scl"),
                            apply_to_scene_models=False,
                            use_splat_xform=True,
                        )
                        applied_splat += 1
                try:
                    self._mgl_log(
                        "scene: apply saved xforms mesh="
                        + str(applied_mesh)
                        + " splat="
                        + str(applied_splat)
                    )
                except Exception:
                    pass
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

        # window coords -> NDC
        x = (2.0 * (float(px) / max(1.0, float(viewport_w)))) - 1.0
        y = 1.0 - (2.0 * (float(py) / max(1.0, float(viewport_h))))  # flip Y
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
                    c = (bmin + bmax) * 0.5
                    cx, cy, cz = float(c[0]), float(c[1]), float(c[2])
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

    # window coords -> NDC
    x = (2.0 * (float(px) / max(1.0, float(viewport_w)))) - 1.0
    y = 1.0 - (2.0 * (float(py) / max(1.0, float(viewport_h))))  # flip Y
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
                c = (bmin + bmax) * 0.5
                cx, cy, cz = float(c[0]), float(c[1]), float(c[2])
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
