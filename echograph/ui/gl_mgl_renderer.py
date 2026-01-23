from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    import openmesh
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


def _mgl_grid(size: float, steps: int) -> "np.ndarray":
    if np is None:
        raise RuntimeError("numpy unavailable")
    u = np.repeat(np.linspace(-size, size, steps), 2)
    v = np.tile([-size, size], steps)
    w = np.zeros(steps * 2)
    grid = np.concatenate([np.dstack([u, v, w]), np.dstack([v, u, w])])
    lower_grid = 0.135
    rotation = np.array(
        [
            [0.0, 0.0, 1.0],
            [1.0, 0.0, 0.0],
            [0.0, lower_grid, 0.0],
        ],
        dtype="f4",
    )
    return np.dot(grid, rotation)


class MGLRendererMixin:
    def _mgl_add_wire_item_from_points(
        self,
        name: str,
        line_points: "np.ndarray",
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
    def _mgl_load_obj_edge_vertices(path: Path) -> "np.ndarray":
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
        self._mgl_grid_model_visible = bool(checked)
        scene = getattr(self, "_mgl_scene", None)
        if not checked:
            if scene is not None:
                scene.set_visible_by_tag("grid-model", False)
            self.update()
            return
        if scene is not None and scene.has_tag("grid-model"):
            scene.set_visible_by_tag("grid-model", True)
            self.update()
            return
        grid_path = Path(r"V:\Source\Repos\EchoMatrixMCP\echograph\3dmodels\grid.obj")
        if not grid_path.exists():
            self._mgl_error = f"Grid model missing: {grid_path}"
            self.update()
            return
        self._mgl_grid_model_pending_path = grid_path
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
        self._mgl_pending_splats = None
        self._mgl_render_splats = False
        self._mgl_splat_count = 0
        self._mgl_splats15_cpu = None

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

    def _mgl_rebuild_scene_splats(self, preserve_camera: bool = False) -> None:
        if np is None:
            self._mgl_disable_splats()
            return
        splat_map = getattr(self, "_mgl_scene_splats", None) or {}
        if not splat_map:
            self._mgl_disable_splats()
            return
        visibility = getattr(self, "_mgl_scene_visibility", {}) or {}
        arrays = [arr for owner, arr in splat_map.items() if visibility.get(owner, True)]
        if not arrays:
            self._mgl_disable_splats()
            return
        state = self._mgl_get_camera_state() if preserve_camera else None
        combined = arrays[0] if len(arrays) == 1 else np.concatenate(arrays, axis=0)
        self.set_splats(combined)
        if state is not None:
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
            self._mgl_prog["Mvp"].write(mvp.astype("f4").tobytes())
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
                color = (float(base_color[0]), float(base_color[1]), float(base_color[2]), float(self._mgl_grid_alpha))
            except Exception:
                color = (0.8, 0.8, 0.8, float(self._mgl_grid_alpha))
        try:
            self._mgl_grid_prog["Mvp"].write(mvp.astype("f4").tobytes())
            self._mgl_grid_prog["Color"].value = color
        except Exception:
            pass
        try:
            try:
                self._mgl_ctx.wireframe = True
            except Exception:
                pass
            try:
                self._mgl_ctx.line_width = float(getattr(self, "_mgl_wire_line_width", 1.0))
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
            self._mgl_wire_prog["Mvp"].write(mvp.astype("f4").tobytes())
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
        points: "np.ndarray",
        normals: "np.ndarray",
        uvs: Optional["np.ndarray"] = None,
        indices: Optional["np.ndarray"] = None,
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
    ) -> Tuple[List[Dict[str, object]], List["np.ndarray"], List[str], int]:
        entries: List[Dict[str, object]] = []
        combined_uvs: List["np.ndarray"] = []
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

        self._mgl_upload_pending_splats()

        scene = getattr(self, "_mgl_scene", None)
        if scene is not None:
            scene.draw(self, mvp)
        # --- SPLATS (instanced-quad only) ---
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

                self._mgl_splatq_prog["Proj"].write(proj.astype("f4"))
                self._mgl_splatq_prog["View"].write(lookat.astype("f4"))
                self._mgl_splatq_prog["Model"].write(model.astype("f4"))

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
            except Exception:
                pass
            self._mgl_splat_bbox_vao.render(moderngl.LINES)
            try:
                self._mgl_ctx.line_width = 1.0
            except Exception:
                pass

        # --- GRID ---
        if self._mgl_grid_vao is not None:
            self._mgl_grid_prog["Mvp"].write(mvp.astype("f4"))
            self._mgl_grid_prog["Color"].value = (0.8, 0.8, 0.8, self._mgl_grid_alpha)
            self._mgl_grid_vao.render(moderngl.LINES)

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

        # scale slider multiplier (the "Scale xx.x" UI)
        try:
            sm = state.get("scale_multiplier", None)
            if sm is not None:
                self._camdbg("[CAM] apply scale_multiplier ->", sm)
                self._camdbg("[CAM] has _example_scale_slider:", bool(getattr(self, "_example_scale_slider", None)))

                self._mgl_scale_multiplier = float(sm)

                if hasattr(self, "_example_scale_slider") and self._example_scale_slider is not None:
                    self._example_scale_slider.setValue(int(self._mgl_scale_multiplier * 100.0))
                    self._camdbg("[CAM] slider now:", self._example_scale_slider.value())

                if hasattr(self, "_example_scale_label") and self._example_scale_label is not None:
                    self._example_scale_label.setText(f"Scale {self._mgl_scale_multiplier:.2f}x")
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

    def _mgl_update_grid(self) -> None:
        if not _HAS_MGL or self._mgl_ctx is None:
            return
        grid = _mgl_grid(self._mgl_grid_size, int(self._mgl_grid_cells))
        grid = grid.astype("f4").reshape(-1, 3)
        self._mgl_grid_vertex_count = int(grid.shape[0])
        self._mgl_grid_vbo = self._mgl_ctx.buffer(grid.tobytes())
        if self._mgl_grid_prog is not None:
            self._mgl_grid_vao = self._mgl_ctx.simple_vertex_array(self._mgl_grid_prog, self._mgl_grid_vbo, "in_position")

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
        points: "np.ndarray",
        normals: "np.ndarray",
        uvs: Optional["np.ndarray"] = None,
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
        combined_points: List["np.ndarray"] = []
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

    def _mgl_set_uv_overlay(self, uvs: Optional["np.ndarray"]) -> None:
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

    def _mgl_init_arcball(self, points: "np.ndarray") -> None:
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
        self._mgl_clear_scene_models()
        self._mgl_submeshes = []
        self._mgl_vao = None
        self._mgl_mesh_vbos = []
        self._mgl_index_buffer = None
        self._mgl_mesh = None
        self._mgl_mesh_vertex_count = 0
        self._mgl_mesh_path = ""
        self._mgl_set_uv_overlay(None)
        # prevent texture leaking from previous "Texture..." or textured Import views
        self._mgl_texture_override = False
        self._mgl_texture = None
        self._mgl_texture_path = ""
        self._mgl_texture_paths = []

        try:
            self._mgl_scene_splats = {}
        except Exception:
            pass

        bounds_min = None
        bounds_max = None
        has_mesh_bounds = False
        has_splats = False
        total_indices = 0
        first_mesh_path = ""

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

                        splats = load_splats_ply(str(path), n=200_000)
                        arr = np.asarray(splats, dtype=np.float32)
                        if arr.ndim == 2 and arr.shape[1] in (8, 10, 14, 15):
                            has_splats = True
                            try:
                                self._mgl_scene_splats[owner] = arr
                            except Exception:
                                pass
                            mins = arr[:, :3].min(axis=0)
                            maxs = arr[:, :3].max(axis=0)
                            bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                    except Exception as exc:
                        self._mgl_error = f"Splat load failed: {exc}"
                    continue

                texture_override = None
                texture_path = str(asset.get("texture", "") or "").strip()
                if texture_path and not self._mgl_texture_override:
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
                            entries, _combined_uvs, _tex_paths, sub_count = self._mgl_build_submesh_entries(
                                mesh_arrays.submeshes
                            )
                        finally:
                            self._mgl_texture_override = original_override
                        if texture_override is not None:
                            for sub in entries:
                                sub["texture"] = texture_override
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
                            bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                            has_mesh_bounds = True
                        elif mesh_arrays.submeshes:
                            for sub in mesh_arrays.submeshes:
                                pts = getattr(sub, "points", None)
                                if pts is None or not getattr(pts, "size", 0):
                                    continue
                                mins = pts.min(axis=0)
                                maxs = pts.max(axis=0)
                                bounds_min, bounds_max = _merge_bounds(bounds_min, bounds_max, mins, maxs)
                                has_mesh_bounds = True
                    else:
                        entry = self._mgl_build_mesh_entry(points, normals, uvs)
                        if entry is None:
                            continue
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

            if has_splats:
                self._mgl_rebuild_scene_splats(preserve_camera=preserve_camera)
            else:
                self._mgl_disable_splats()

        except Exception as exc:
            self._mgl_error = f"Scene load failed: {exc}"
        finally:
            if did_make_current:
                try:
                    self.doneCurrent()
                except Exception:
                    pass
