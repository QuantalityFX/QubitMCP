from __future__ import annotations

import math
from pathlib import Path
import struct
import tempfile
import time
from array import array
from typing import  Dict, List, Optional, Tuple
from .gl_shaders import SHADERS
from .gl_scene import MGLScene
from .gl_types import ModelData, MeshArrays, SubMeshData
from .gl_glutils import build_qt_program
from .gl_glutils import upload_scene_texture
from .gl_glutils import update_quad_vbo
from .gl_glutils import float_bytes
from .gl_debug_geo import debug_cube_vertices, debug_cube_wire_vertices
from .gl_mesh import _GLMesh
from .gl_mgl_renderer import MGLRendererMixin
from .gl_loaders import ensure_assimp_dll
from .gl_loaders import load_fbx_mesh_arrays_pyassimp
from .gl_loaders import load_gltf_model
from .gl_loaders import load_model
from .gl_loaders import register_model_loader

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

ensure_assimp_dll()

try:
    import trimesh
except Exception:
    trimesh = None


try:
    from PySide6 import QtCore, QtGui, QtWidgets
    _HAS_QT6 = True
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
    _HAS_QT6 = False

QOpenGLWidget = None  # type: ignore
QOpenGLShader = None  # type: ignore
QOpenGLShaderProgram = None  # type: ignore
QOpenGLTexture = None  # type: ignore
QOpenGLBuffer = None  # type: ignore
QOpenGLVertexArrayObject = None  # type: ignore

if _HAS_QT6:
    try:
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
    except Exception:
        QOpenGLWidget = None  # type: ignore
    try:
        from PySide6 import QtGui as _QtGui  # type: ignore
        QOpenGLShader = getattr(_QtGui, "QOpenGLShader", None)
        QOpenGLShaderProgram = getattr(_QtGui, "QOpenGLShaderProgram", None)
        QOpenGLTexture = getattr(_QtGui, "QOpenGLTexture", None)
        QOpenGLBuffer = getattr(_QtGui, "QOpenGLBuffer", None)
        QOpenGLVertexArrayObject = getattr(_QtGui, "QOpenGLVertexArrayObject", None)
    except Exception:
        QOpenGLShader = None  # type: ignore
        QOpenGLShaderProgram = None  # type: ignore
        QOpenGLTexture = None  # type: ignore
        QOpenGLBuffer = None  # type: ignore
    if (
        QOpenGLShader is None
        or QOpenGLShaderProgram is None
        or QOpenGLTexture is None
        or QOpenGLBuffer is None
        or QOpenGLVertexArrayObject is None
    ):
        try:
            from PySide6 import QtOpenGL as _QtOpenGL  # type: ignore
            if QOpenGLShader is None:
                QOpenGLShader = getattr(_QtOpenGL, "QOpenGLShader", None)
            if QOpenGLShaderProgram is None:
                QOpenGLShaderProgram = getattr(_QtOpenGL, "QOpenGLShaderProgram", None)
            if QOpenGLTexture is None:
                QOpenGLTexture = getattr(_QtOpenGL, "QOpenGLTexture", None)
            if QOpenGLBuffer is None:
                QOpenGLBuffer = getattr(_QtOpenGL, "QOpenGLBuffer", None)
            if QOpenGLVertexArrayObject is None:
                QOpenGLVertexArrayObject = getattr(_QtOpenGL, "QOpenGLVertexArrayObject", None)
        except Exception:
            pass
else:
    try:
        from PySide2.QtWidgets import QOpenGLWidget  # type: ignore
    except Exception:
        QOpenGLWidget = None  # type: ignore
    try:
        from PySide2 import QtGui as _QtGui  # type: ignore
        QOpenGLShader = getattr(_QtGui, "QOpenGLShader", None)
        QOpenGLShaderProgram = getattr(_QtGui, "QOpenGLShaderProgram", None)
        QOpenGLTexture = getattr(_QtGui, "QOpenGLTexture", None)
        QOpenGLBuffer = getattr(_QtGui, "QOpenGLBuffer", None)
        QOpenGLVertexArrayObject = getattr(_QtGui, "QOpenGLVertexArrayObject", None)
    except Exception:
        QOpenGLShader = None  # type: ignore
        QOpenGLShaderProgram = None  # type: ignore
        QOpenGLTexture = None  # type: ignore
        QOpenGLBuffer = None  # type: ignore
    if (
        QOpenGLShader is None
        or QOpenGLShaderProgram is None
        or QOpenGLTexture is None
        or QOpenGLBuffer is None
        or QOpenGLVertexArrayObject is None
    ):
        try:
            from PySide2 import QtOpenGL as _QtOpenGL  # type: ignore
            if QOpenGLShader is None:
                QOpenGLShader = getattr(_QtOpenGL, "QOpenGLShader", None)
            if QOpenGLShaderProgram is None:
                QOpenGLShaderProgram = getattr(_QtOpenGL, "QOpenGLShaderProgram", None)
            if QOpenGLTexture is None:
                QOpenGLTexture = getattr(_QtOpenGL, "QOpenGLTexture", None)
            if QOpenGLBuffer is None:
                QOpenGLBuffer = getattr(_QtOpenGL, "QOpenGLBuffer", None)
            if QOpenGLVertexArrayObject is None:
                QOpenGLVertexArrayObject = getattr(_QtOpenGL, "QOpenGLVertexArrayObject", None)
        except Exception:
            pass

# OpenGL constants (avoid optional PyOpenGL dependency).
GL_TRIANGLES = 0x0004
GL_TRIANGLE_STRIP = 0x0005
GL_LINES = 0x0001
GL_FLOAT = 0x1406
GL_UNSIGNED_SHORT = 0x1403
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_CULL_FACE = 0x0B44
GL_LESS = 0x0201
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_DEPTH_TEST = 0x0B71


def load_gltf_model(path: Path) -> ModelData:
    if path.suffix.lower() == ".glb":
        gltf, buffers = _read_glb(path)
    else:
        gltf, buffers = _read_gltf(path)
    meshes = gltf.get("meshes", []) or []
    nodes = gltf.get("nodes", []) or []
    scene_index = gltf.get("scene", 0) or 0
    scenes = gltf.get("scenes", []) or []
    node_roots = []
    if scenes and scene_index < len(scenes):
        node_roots = scenes[scene_index].get("nodes", []) or []
    elif nodes:
        node_roots = list(range(len(nodes)))

    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    node_pairs = _gltf_collect_nodes(gltf, node_roots, identity)

    vertices: List[float] = []
    bounds = [math.inf, math.inf, math.inf, -math.inf, -math.inf, -math.inf]

    for node, world in node_pairs:
        mesh_index = node.get("mesh")
        if mesh_index is None or mesh_index >= len(meshes):
            continue
        mesh = meshes[mesh_index]
        for prim in mesh.get("primitives", []) or []:
            attrs = prim.get("attributes") or {}
            pos_accessor = attrs.get("POSITION")
            if pos_accessor is None:
                continue
            positions = _read_accessor(gltf, buffers, int(pos_accessor))
            indices = None
            if "indices" in prim:
                idx_data = _read_accessor(gltf, buffers, int(prim["indices"]))
                indices = [int(i) for i in idx_data]
            tri_mode = int(prim.get("mode", 4))
            if tri_mode != 4:
                continue
            primitive_vertices: List[float] = []
            if indices:
                for idx in indices:
                    base = idx * 3
                    x, y, z = positions[base], positions[base + 1], positions[base + 2]
                    x, y, z = _apply_mat4(world, x, y, z)
                    primitive_vertices.extend([x, y, z])
            else:
                for i in range(0, len(positions), 3):
                    x, y, z = positions[i], positions[i + 1], positions[i + 2]
                    x, y, z = _apply_mat4(world, x, y, z)
                    primitive_vertices.extend([x, y, z])
            for i in range(0, len(primitive_vertices), 3):
                vx, vy, vz = (
                    primitive_vertices[i],
                    primitive_vertices[i + 1],
                    primitive_vertices[i + 2],
                )
                bounds[0] = min(bounds[0], vx)
                bounds[1] = min(bounds[1], vy)
                bounds[2] = min(bounds[2], vz)
                bounds[3] = max(bounds[3], vx)
                bounds[4] = max(bounds[4], vy)
                bounds[5] = max(bounds[5], vz)
            vertices.extend(primitive_vertices)

    if not vertices:
        bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    return ModelData(vertices=vertices, bounds=tuple(bounds))


def _load_obj_model(path: Path) -> ModelData:
    positions: List[Tuple[float, float, float]] = []
    vertices: List[float] = []
    bounds = [math.inf, math.inf, math.inf, -math.inf, -math.inf, -math.inf]

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
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            except Exception:
                continue
            positions.append((x, y, z))
        elif head == "f" and len(parts) >= 4:
            indices: List[int] = []
            for token in parts[1:]:
                if not token:
                    continue
                idx_str = token.split("/")[0]
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
                indices.append(idx - 1)
            if len(indices) < 3:
                continue
            root = indices[0]
            for i in range(1, len(indices) - 1):
                tri = (root, indices[i], indices[i + 1])
                for vidx in tri:
                    try:
                        vx, vy, vz = positions[vidx]
                    except Exception:
                        continue
                    vertices.extend([vx, vy, vz])
                    bounds[0] = min(bounds[0], vx)
                    bounds[1] = min(bounds[1], vy)
                    bounds[2] = min(bounds[2], vz)
                    bounds[3] = max(bounds[3], vx)
                    bounds[4] = max(bounds[4], vy)
                    bounds[5] = max(bounds[5], vz)

    if not vertices:
        bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    return ModelData(vertices=vertices, bounds=tuple(bounds))


def _normalize_color(value: object) -> Optional[Tuple[float, float, float, float]]:
    try:
        vals = [float(v) for v in value]
    except Exception:
        return None
    if not vals:
        return None
    if len(vals) == 3:
        vals.append(1.0)
    vals = vals[:4]
    if max(vals) > 1.0:
        vals = [v / 255.0 for v in vals]
    return tuple(max(0.0, min(1.0, v)) for v in vals)


def _extract_trimesh_material(mesh, path: Path) -> Tuple[Optional[Path], Optional[object], Optional[Tuple[float, float, float, float]]]:
    material = getattr(getattr(mesh, "visual", None), "material", None)
    if material is None:
        colors = getattr(getattr(mesh, "visual", None), "vertex_colors", None)
        if np is not None and colors is not None:
            try:
                avg = np.mean(np.asarray(colors), axis=0)
                return None, None, _normalize_color(avg)
            except Exception:
                pass
        return None, None, None
    base_color = None
    for attr in ("baseColorFactor", "diffuse", "ambient", "color"):
        val = getattr(material, attr, None)
        if val is not None:
            base_color = _normalize_color(val)
            if base_color is not None:
                break
    texture_path = None
    image_path = getattr(material, "image_path", None)
    if image_path:
        candidate = Path(str(image_path))
        if not candidate.is_absolute():
            candidate = (path.parent / candidate).resolve()
        if candidate.exists():
            texture_path = candidate
    texture_image = getattr(material, "image", None)
    return texture_path, texture_image, base_color


def _load_fbx_mesh_arrays(path: Path) -> MeshArrays:
    if np is None:
        raise RuntimeError("numpy unavailable")
    ensure_assimp_dll()
    use_trimesh = trimesh is not None
    if use_trimesh:
        try:
            from trimesh.exchange import load as trimesh_load
            if "fbx" not in trimesh_load.mesh_loaders:
                use_trimesh = False
        except Exception:
            use_trimesh = False

    if use_trimesh:
        scene = trimesh.load(path, force="scene")
        meshes = []
        if isinstance(scene, trimesh.Scene):
            for geom in scene.geometry.values():
                if getattr(geom, "faces", None) is not None:
                    meshes.append(geom)
        else:
            meshes = [scene]
        if not meshes:
            raise RuntimeError("FBX mesh missing")
    else:
        return load_fbx_mesh_arrays_pyassimp(path)

    points_all: List["np.ndarray"] = []
    normals_all: List["np.ndarray"] = []
    uvs_all: List["np.ndarray"] = []
    submeshes: List[SubMeshData] = []
    texture_path = None
    texture_image = None
    base_color = None

    for mesh in meshes:
        faces = getattr(mesh, "faces", None)
        vertices = getattr(mesh, "vertices", None)
        if faces is None or vertices is None:
            continue
        faces = np.asarray(faces, dtype=np.int64)
        if faces.size == 0:
            continue
        vertices = np.asarray(vertices, dtype="f4")
        tri_vertices = vertices[faces].reshape(-1, 3)

        mesh_normals = getattr(mesh, "vertex_normals", None)
        if mesh_normals is None or len(mesh_normals) != len(vertices):
            v0 = vertices[faces[:, 0]]
            v1 = vertices[faces[:, 1]]
            v2 = vertices[faces[:, 2]]
            n = np.cross(v1 - v0, v2 - v0)
            lengths = np.linalg.norm(n, axis=1)
            lengths[lengths < 1e-6] = 1.0
            n = (n.T / lengths).T
            tri_normals = np.repeat(n[:, None, :], 3, axis=1).reshape(-1, 3)
        else:
            normals = np.asarray(mesh_normals, dtype="f4")
            tri_normals = normals[faces].reshape(-1, 3)

        uv = None
        visual = getattr(mesh, "visual", None)
        if visual is not None and getattr(visual, "uv", None) is not None:
            uv_raw = np.asarray(visual.uv, dtype="f4")
            if uv_raw.shape[0] == vertices.shape[0]:
                uv = uv_raw[faces].reshape(-1, 2)
        if uv is None:
            uv = np.zeros((tri_vertices.shape[0], 2), dtype="f4")

        tri_vertices = tri_vertices.astype("f4")
        tri_normals = tri_normals.astype("f4")
        uv = uv.astype("f4")
        points_all.append(tri_vertices)
        normals_all.append(tri_normals)
        uvs_all.append(uv)

        t_path, t_image, color = _extract_trimesh_material(mesh, path)
        submeshes.append(
            SubMeshData(
                points=tri_vertices,
                normals=tri_normals,
                uvs=uv,
                texture_path=t_path,
                texture_image=t_image,
                base_color=color,
            )
        )

        if texture_path is None and texture_image is None:
            if t_path is not None or t_image is not None:
                texture_path = t_path
                texture_image = t_image
        if base_color is None and color is not None:
            base_color = color

    if not points_all:
        raise RuntimeError("FBX mesh empty")

    points = np.concatenate(points_all, axis=0)
    normals = np.concatenate(normals_all, axis=0)
    uvs = np.concatenate(uvs_all, axis=0)
    return MeshArrays(
        points=points,
        normals=normals,
        uvs=uvs,
        texture_path=texture_path,
        texture_image=texture_image,
        base_color=base_color,
        submeshes=submeshes if submeshes else None,
    )


def _load_fbx_model(path: Path) -> ModelData:
    mesh_arrays = _load_fbx_mesh_arrays(path)
    points = mesh_arrays.points
    bounds = [
        float(np.min(points[:, 0])),
        float(np.min(points[:, 1])),
        float(np.min(points[:, 2])),
        float(np.max(points[:, 0])),
        float(np.max(points[:, 1])),
        float(np.max(points[:, 2])),
    ]
    vertices = points.reshape(-1).astype("f4").tolist()
    return ModelData(vertices=vertices, bounds=tuple(bounds))

register_model_loader([".gltf", ".glb"], load_gltf_model)
register_model_loader([".obj"], _load_obj_model)
register_model_loader([".fbx"], _load_fbx_model)

_HAS_MGL = moderngl is not None and np is not None and Matrix44 is not None

print("[GL_VIEW] LOADED FROM:", __file__)

class GraphGLView(MGLRendererMixin, QOpenGLWidget if QOpenGLWidget is not None else QtWidgets.QWidget):
    def __init__(self, scene, parent=None):
        print("[GL_VIEW] INIT FROM:", __file__)
        super().__init__(parent)
        # --- Axis overlay (debug) and xform gizmo state ---
        self._axis_overlay = None
        self._debug_show_axis_overlay = False

        self._xform_gizmo_pos = (0.0, 0.0, 0.0)
        self._xform_gizmo_owner = None
        self._xform_gizmo_owner_kind = None
        self._xform_gizmo_pos_locked = False
        self._xform_gizmo_idle_visible = True
        self._xform_gizmo_mode = "translate"

        self._xform_rotate_dragging = False
        self._xform_rotate_axis = None
        self._xform_rotate_owner = None
        self._xform_rotate_kind = None
        self._xform_rotate_start_angle = None
        self._xform_rotate_start_rot = None
        self._xform_rotate_center = None
        self._xform_drag_kind = None

        try:
            from .axis_gizmo_overlay import AxisGizmoOverlay
            self._axis_overlay = AxisGizmoOverlay()
            self._debug_show_axis_overlay = True
            print("[AXIS_OVERLAY] ready")
        except Exception as exc:
            self._axis_overlay = None
            self._debug_show_axis_overlay = False
            print("[AXIS_OVERLAY] disabled (boot-safe):", exc)

        try:
            from echograph.gizmo.rotate_gizmo_shared import RotateGizmoShared
            self._rot_shared = RotateGizmoShared()
            print("[ROT_SHARED] ready")
        except Exception as exc:
            self._rot_shared = None
            print("[ROT_SHARED] disabled (boot-safe):", exc)
            

            self._debug_show_axis_overlay = False
            print("[AXIS_OVERLAY] disabled (boot-safe):", exc)

        self._dbg_id = f"{id(self):x}"
        print(f"[GL_VIEW] INSTANCE NEW {self._dbg_id}")

        try:
            self.destroyed.connect(lambda *_: print(f"[GL_VIEW] INSTANCE DESTROYED {self._dbg_id}"))
        except Exception:
            pass

        if QOpenGLWidget is not None:
            try:
                fmt = QtGui.QSurfaceFormat()
                fmt.setDepthBufferSize(24)
                fmt.setStencilBufferSize(8)
                self.setFormat(fmt)
            except Exception:
                pass
            try:
                if hasattr(self, "setUpdateBehavior"):
                    behavior = None
                    if hasattr(QOpenGLWidget, "NoPartialUpdate"):
                        behavior = QOpenGLWidget.NoPartialUpdate
                    else:
                        behavior = getattr(
                            getattr(QOpenGLWidget, "UpdateBehavior", None),
                            "NoPartialUpdate",
                            None,
                        )
                    if behavior is not None:
                        self.setUpdateBehavior(behavior)
            except Exception:
                pass
        #DEBUG LOG Toggle
        self._mgl_debug = False
        self._mgl_cam_debug = False
        self._thumb_debug = False
        # camera debug log (same folder as the main EchoGraph log)
        try:
            log_dir = Path(tempfile.gettempdir()) / "EchoGraph"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._cam_debug_log_path = log_dir / f"echograph_cam_debug_{time.strftime('%Y%m%d')}.log"
        except Exception:
            self._cam_debug_log_path = Path("echograph_cam_debug.log")
        self._scene = scene
        self._scene_texture = None
        self._scene_texture_dirty = False
        self._scene_src_rect = QtCore.QRectF()
        self._quad_vbo = None
        self._quad_ready = False
        self._vao = None
        self._quad_program = None
        self._mesh_program = None
        self._quad_pos_loc = -1
        self._quad_uv_loc = -1
        self._mesh_pos_loc = -1
        self._shader_error = ""
        self._debug_overlay = False
        self._mipmaps_enabled = False
        self._capture_view = None
        self._scene_content_blank = False
        self._debug_mesh = None
        self._debug_wire = None
        self._debug_mesh_color = QtGui.QColor("#94a3b8")
        self._debug_wire_color = QtGui.QColor("#f97316")
        self._debug_mesh_scale = 1.0
        self._debug_mesh_scale_base = 1.0
        self._debug_mesh_center = QtCore.QPointF(0.0, 0.0)
        self._grid_vertices: List[float] = []
        self._grid_vbo = None
        self._grid_count = 0
        self._grid_dirty = True
        self._grid_color = QtGui.QColor("#e2e8f0")
        self._grid_center = QtCore.QPointF(0.0, 0.0)
        self._grid_z = -0.6
        self._meshes: Dict[str, _GLMesh] = {}
        self._mesh_colors: Dict[str, QtGui.QColor] = {}
        self._mesh_transforms: Dict[str, QtGui.QMatrix4x4] = {}
        self._mesh_meta: Dict[str, dict] = {}
        self._model_scale_multiplier = 1.0
        self._manual_model_path: Optional[Path] = None
        self._auto_frame_on_scale = False
        self._world_extent = 100.0
        self._cube_size = 1.0
        self._render_scene_plane = False
        self._render_scene_models = False
        self._show_test_cube = False
        self._show_scene_plane = False
        self._use_ortho = True
        self._test_cam_locked = False
        self._use_moderngl = True
        self._use_example_pipeline = False
        self._model_load_pending = False
        self._render_paused = False
        self._mgl_pending_cam_state = None
        self._drag_divisor = 13.0
        self._zoom_multiplier = 1.1
        self._min_cam_dist = 5.0
        self._max_cam_dist = 500.0
        self._orbit_sensitivity = 0.005
        
        self._cam_yaw = 0.45
        self._cam_pitch = -0.35
        self._cam_dist = 10.0
        self._cam_focal = 1200.0
        self._fov_deg = 50.0
        self._cam_target = QtCore.QPointF(0.0, 0.0)

        self._example_program = None
        self._example_vbo = None
        self._example_ibo = None
        self._example_vao = None
        self._example_index_count = 0
        self._example_draw_count = 0
        self._example_pending_vertices: Optional[List[float]] = None
        self._example_pending_bounds: Optional[Tuple[float, float, float, float, float, float]] = None
        self._example_model_center = (0.0, 0.0, 0.0)
        self._example_model_base_scale = 1.0
        self._example_model_scale = 1.0
        self._example_model_path = ""
        self._example_model_extent = 1.0
        self._example_pending_count = 0
        self._example_proj = QtGui.QMatrix4x4()
        self._example_transform = QtGui.QMatrix4x4()
        self._example_cam_pos = QtGui.QVector3D(-30.0, 30.0, 40.0)
        self._example_cam_look = QtGui.QVector3D(0.0, 0.0, 0.0)
        self._example_cam_up = QtGui.QVector3D(0.0, 1.0, 0.0)
        self._example_cam_right = QtGui.QVector3D(1.0, 0.0, 0.0)
        self._example_fov = 30.0
        self._example_near = 2.0
        self._example_far = 200.0
        self._example_pan_speed = 0.02
        self._example_rotate_speed = 0.3
        self._example_zoom_step = 2.0
        self._viewport_bg = QtGui.QColor("#535353")  # light gray
        self._example_clear_color = QtGui.QColor(self._viewport_bg)
        self._example_grid_vbo = None
        self._example_grid_count = 0
        self._example_grid_extent = 12.0
        self._mgl_bg_color = (
            self._viewport_bg.redF(),
            self._viewport_bg.greenF(),
            self._viewport_bg.blueF(),
            1.0,
        )
        self._mgl_splat_prog = None
        self._mgl_splat_vbo = None
        self._mgl_splat_vao = None
        
        # instanced-quad splats (new path)
        self._mgl_splatq_prog = None
        self._mgl_splatq_quad_vbo = None   # static quad corners
        self._mgl_splatq_vbo = None        # instance buffer (Nx8)
        self._mgl_splatq_vao = None
        self._mgl_splat_world_scale = 3.0  # tuning knob
        self._mgl_splat_sort_tick = 0
        self._mgl_splat_count = 0
        self._mgl_pending_splats = None
        self._mgl_render_splats = False
        self._mgl_splats_visibility_dirty = False
        self._mgl_splats_need_rebuild = False
        self._mgl_visibility_dirty = False
        self._mgl_pending_visibility: Dict[str, bool] = {}
        self._mgl_ctx = None
        self._mgl_prog = None
        self._mgl_grid_prog = None
        self._mgl_wire_prog = None
        self._mgl_mesh = None
        self._mgl_vao = None
        self._mgl_grid_vao = None
        self._mgl_grid_vbo = None
        self._mgl_grid_model_visible = False
        self._mgl_grid_model_vao = None
        self._mgl_grid_model_vbo = None
        self._mgl_grid_model_nbo = None
        self._mgl_grid_model_tbo = None
        self._mgl_grid_model_ibo = None
        self._mgl_grid_model_count = 0
        self._mgl_grid_model_pending_path = None
        self._mgl_scene = MGLScene()
        self._mgl_scene_visibility: Dict[str, bool] = {}
        self._mgl_scene_splats: Dict[str, "np.ndarray"] = {}
        self._mgl_scene_splats_bounds_local: Dict[str, "np.ndarray"] = {}
        self._mgl_scene_splat_bounds_by_owner: Dict[str, "np.ndarray"] = {}
        self._mgl_scene_mesh_bounds_by_owner: Dict[str, "np.ndarray"] = {}
        self._mgl_scene_splat_xforms_by_owner: Dict[str, Dict[str, Tuple[float, float, float]]] = {}
        self._mgl_splat_bbox_vao = None
        self._mgl_splat_bbox_vbo = None
        self._mgl_mesh_vbos = []
        self._mgl_index_buffer = None
        self._mgl_mesh_path = ""
        self._mgl_texture = None
        self._mgl_texture_path = ""
        self._mgl_texture_paths: List[str] = []
        self._mgl_texture_override = False
        self._mgl_submeshes: List[Dict[str, object]] = []
        self._mgl_error = ""
        self._mgl_wireframe = False
        self._mgl_cull_enabled = False
        self._mgl_bg_color = (
            self._viewport_bg.redF(),
            self._viewport_bg.greenF(),
            self._viewport_bg.blueF(),
            1.0,
        )
        self._mgl_mesh_color = (0.85, 0.88, 0.95, 1.0)
        self._mgl_light_intensity = 1.0
        self._mgl_wire_color = (0.25, 0.25, 0.25, 1.0)
        self._mgl_wire_line_width = 1.0
        self._mgl_wire_edge_width = 2.0
        self._mgl_uv_overlay_enabled = False
        self._mgl_uv_segments: List[Tuple[float, float, float, float]] = []
        self._mgl_uv_bounds: Optional[Tuple[float, float, float, float]] = None
        self._mgl_uv_vertex_count = 0
        self._mgl_uv_cache = None
        self._mgl_uv_cache_rect = QtCore.QRectF()
        self._mgl_grid_alpha = 0.90
        self._mgl_grid_size = 20.0
        self._mgl_grid_cells = 50
        self._mgl_fov = 60.0
        self._mgl_clip_far = 1000.0
        self._mgl_camera_zoom = 2.0
        self._mgl_center = None
        self._mgl_base_center = None
        self._mgl_base_zoom = None
        self._mgl_scale = 1.0
        self._mgl_scale_multiplier = 1.0
        self._mgl_arcball = None
        self._mgl_grid_vertex_count = 0
        self._mgl_mesh_vertex_count = 0
        self._mgl_prev_x = 0
        self._mgl_prev_y = 0
        self._mgl_zoom_press_pos = None
        self._mgl_zoom_start = None

        # --- IM3D bridge (safe no-op by default) ---
        self._im3d = None
        try:
            from .im3d_bridge import Im3dBridge
            self._im3d = Im3dBridge()  # constructor must not touch GL
            print("[IM3D] Bridge created")
        except Exception as exc:
            self._im3d = None
            print("[IM3D] Bridge disabled:", exc)

        if self._use_moderngl and not _HAS_MGL:
            self._use_moderngl = False
            self._use_example_pipeline = True

        self._orbit_dragging = False
        self._pan_dragging = False
        self._dolly_dragging = False
        self._orbit_last_pos = None
        self._pan_last_pos = None
        self._dolly_press_pos = None
        self._dolly_start_dist = None
        self._debug_toggle_icon_active = None
        self._debug_toggle_icon_inactive = None
        self._debug_overlay_cache = None
        self._debug_overlay_cache_key = None

        self._fps = 0.0
        self._fps_last_t = time.perf_counter()
        self._fps_ema = 0.0   # smoothed dt
        self._fps_timer = QtCore.QTimer(self)
        self._fps_timer.setInterval(16)  # ~60hz
        #self._fps_timer.setInterval(100)  # 10hz idle refresh
        self._fps_timer.timeout.connect(self.update)
        #self._fps_timer.start()

        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._build_scale_controls()
        self._build_debug_toggle_button()
        self._build_debug_copy_button()
        


    def debug_points(self) -> None:
        if np is None:
            return
        pts = np.array([
            [0.0, 0.0, 0.0,  1.0, 0.0, 1.0, 1.0,  1.0],
            [10.0, 0.0, 0.0,  1.0, 0.0, 1.0, 1.0,  1.0],
            [0.0, 10.0, 0.0,  1.0, 0.0, 1.0, 1.0,  1.0],
        ], dtype=np.float32)
        self.set_splats(pts)

    def set_scene(self, scene) -> None:
        self._scene = scene

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_controls", None) is not None:
            h = int(getattr(self, "_controls_h", 44))
            self._controls.setGeometry(0, max(0, self.height() - h), self.width(), h)
        toggle = getattr(self, "_debug_toggle_btn", None)
        if toggle is not None:
            toggle.setGeometry(10, 10, 22, 22)
        btn = getattr(self, "_debug_copy_btn", None)
        if btn is not None:
            btn.setGeometry(38, 10, 46, 22)
        if getattr(self, "_mgl_uv_cache", None) is not None:
            self._mgl_uv_cache = None

    def showEvent(self, e):
        super().showEvent(e)
        if hasattr(self, "_fps_timer"):
            self._fps_timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        if hasattr(self, "_fps_timer"):
            self._fps_timer.stop()

    def paintEvent(self, event):
        if QOpenGLWidget is None:
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor("#0f172a"))
            painter.setPen(QtGui.QColor("#e2e8f0"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "OpenGL widgets not available.")
            painter.end()
            return
        super().paintEvent(event)
        if not hasattr(self, "_gl"):
            painter = QtGui.QPainter(self)
            painter.fillRect(self.rect(), QtGui.QColor("#0f172a"))
            painter.end()
        # Reset GL texture unit for Qt's QPainter overlay (prevents textured/black stats panel)
        if hasattr(self, "_gl"):
            try:
                self._gl.glActiveTexture(0x84C0)  # GL_TEXTURE0
            except Exception:
                pass
        self._draw_overlay()
        self._draw_rotate_shared_overlay()

    def refresh_from_scene(self) -> None:
        if self._render_scene_plane:
            self._capture_scene_texture()
        else:
            self._scene_texture = None
            self._scene_texture_dirty = False
            self._pending_image = None
            self._scene_src_rect = QtCore.QRectF()
            self._scene_content_blank = True
        if self._render_scene_models:
            self._load_scene_models()
        else:
            self._meshes.clear()
            self._mesh_colors.clear()
            self._mesh_transforms.clear()
            self._mesh_meta.clear()
        self._reset_camera()
        self.update()

    def _build_scale_controls(self) -> None:
        try:
            controls = QtWidgets.QFrame(self)
            controls.setObjectName("GLControls")
            controls.setStyleSheet(
                "#GLControls{background:rgba(15,23,42,210);border-top:1px solid #334155;}"
                "#GLControls QLabel{color:#e2e8f0;font-size:11px;}"
                "#GLControls QPushButton{padding:3px 10px;font-weight:600;color:#e2e8f0;"
                "background:#1f2937;border-radius:4px;}"
                "#GLControls QPushButton:hover{background:#334155;}"
            )
            layout = QtWidgets.QHBoxLayout(controls)
            layout.setContentsMargins(10, 6, 10, 6)
            layout.setSpacing(10)

            self._frame_btn = QtWidgets.QPushButton()
            self._frame_btn.setToolTip("Frame")
            frame_icon = Path(__file__).resolve().parents[2] / "icons" / "Frame_Icon.png"
            if frame_icon.exists():
                self._frame_btn.setIcon(QtGui.QIcon(str(frame_icon)))
                self._frame_btn.setIconSize(QtCore.QSize(16, 16))
            else:
                self._frame_btn.setText("Frame")
            self._frame_btn.clicked.connect(self._on_frame_clicked)
            layout.addWidget(self._frame_btn, 0)

            self._camlog_btn = QtWidgets.QPushButton("LogCam")
            self._camlog_btn.setToolTip("Write current camera/orbit state to log")
            self._camlog_btn.clicked.connect(self._on_camlog_clicked)
            layout.addWidget(self._camlog_btn, 0)

            self._snapgrab_btn = QtWidgets.QPushButton()
            self._snapgrab_btn.setToolTip("Snapshot")
            icon_path = Path(__file__).resolve().parents[2] / "icons" / "screengrab _Icon_s_001.png"
            if icon_path.exists():
                self._snapgrab_btn.setIcon(QtGui.QIcon(str(icon_path)))
            self._snapgrab_btn.clicked.connect(self._on_snapgrab_clicked)
            layout.addWidget(self._snapgrab_btn, 0)
            
            self._example_model_btn = QtWidgets.QPushButton("Model...")
            if self._use_moderngl:
                self._example_model_btn.clicked.connect(self._on_mgl_pick_model)
            else:
                self._example_model_btn.clicked.connect(self._on_example_pick_model)
            layout.addWidget(self._example_model_btn, 0)
            if self._use_moderngl:
                self._mgl_texture_btn = QtWidgets.QPushButton("Texture...")
                self._mgl_texture_btn.clicked.connect(self._on_mgl_pick_texture)
                layout.addWidget(self._mgl_texture_btn, 0)
            self._example_scale_label = QtWidgets.QLabel("Scale 1.00x")
            try:
                fm = self._example_scale_label.fontMetrics()
                self._example_scale_label.setFixedWidth(fm.horizontalAdvance("Scale 20.00x") + 6)
                self._example_scale_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            except Exception:
                pass
            self._example_scale_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self._example_scale_slider.setRange(1, 2000)
            scale_val = self._mgl_scale_multiplier if self._use_moderngl else self._example_model_scale
            self._example_scale_slider.setValue(int(scale_val * 100))
            self._example_scale_slider.setFixedWidth(160)
            if self._use_moderngl:
                self._example_scale_slider.valueChanged.connect(self._on_mgl_scale_changed)
            else:
                self._example_scale_slider.valueChanged.connect(self._on_example_scale_changed)
            layout.addWidget(self._example_scale_label, 0)
            layout.addWidget(self._example_scale_slider, 0)
            if self._use_moderngl:
                self._mgl_light_label = QtWidgets.QLabel("Light 1.00x")
                self._mgl_light_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                self._mgl_light_slider.setRange(0, 1000)
                self._mgl_light_slider.setValue(int(self._mgl_light_intensity * 100))
                self._mgl_light_slider.setFixedWidth(140)
                self._mgl_light_slider.valueChanged.connect(self._on_mgl_light_changed)
                layout.addWidget(self._mgl_light_label, 0)
                layout.addWidget(self._mgl_light_slider, 0)
                self._mgl_clip_label = QtWidgets.QLabel("Clip")
                self._mgl_clip_input = QtWidgets.QLineEdit(str(int(self._mgl_clip_far)))
                self._mgl_clip_input.setFixedWidth(70)
                self._mgl_clip_input.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                try:
                    self._mgl_clip_input.setValidator(QtGui.QIntValidator(100, 100000, self))
                except Exception:
                    pass
                self._mgl_clip_input.editingFinished.connect(self._on_mgl_clip_changed)
                layout.addWidget(self._mgl_clip_label, 0)
                layout.addWidget(self._mgl_clip_input, 0)
                self._mgl_wireframe_toggle = QtWidgets.QCheckBox("Wireframe")
                self._mgl_wireframe_toggle.setChecked(bool(self._mgl_wireframe))
                self._mgl_wireframe_toggle.toggled.connect(self._on_mgl_wireframe_toggled)
                layout.addWidget(self._mgl_wireframe_toggle, 0)

                self._mgl_grid_toggle = QtWidgets.QCheckBox("Grid")

                # Set initial state WITHOUT triggering the handler
                self._mgl_grid_toggle.blockSignals(True)
                self._mgl_grid_toggle.setChecked(bool(getattr(self, "_mgl_grid_visible", False)))
                self._mgl_grid_toggle.blockSignals(False)

                # Now connect and run once to sync/cleanup
                self._mgl_grid_toggle.toggled.connect(self._on_mgl_grid_toggled)
                self._on_mgl_grid_toggled(self._mgl_grid_toggle.isChecked())

                layout.addWidget(self._mgl_grid_toggle, 0)
                self._mgl_uv_toggle = QtWidgets.QCheckBox("UVs")
                self._mgl_uv_toggle.setChecked(bool(self._mgl_uv_overlay_enabled))
                self._mgl_uv_toggle.toggled.connect(self._on_mgl_uv_toggled)
                layout.addWidget(self._mgl_uv_toggle, 0)

            layout.addStretch(1)
            self._controls = controls
            self._controls_h = 44
            self._controls.show()
        except Exception:
            self._controls = None
            self._controls_h = 0

    def _on_camlog_clicked(self) -> None:
        self._write_camera_debug_snapshot()

    def _write_camera_debug_snapshot(self) -> None:
        try:
            # rebuild the same matrices used in _paint_mgl
            zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))

            lookat = Matrix44.look_at(
                (0.0, 0.0, zoom),
                (0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
            )

            # arcball transform (same recenter logic)
            transform = Matrix44.identity(dtype="f4")
            center = getattr(self, "_mgl_center", None)
            arc = getattr(self, "_mgl_arcball", None)
            if arc is not None:
                try:
                    if center is not None and np is not None:
                        arc.Transform[3, :3] = -arc.Transform[:3, :3].T @ center
                except Exception:
                    pass
                try:
                    src = arc.Transform
                    if hasattr(src, "tolist"):
                        transform = Matrix44(src.tolist(), dtype="f4")
                    else:
                        transform = Matrix44(src, dtype="f4")
                except Exception:
                    transform = Matrix44.identity(dtype="f4")

            # optional: determinant of rotation part (detect flips/mirroring)
            det = None
            try:
                if np is not None:
                    rot3 = np.array(transform[:3, :3], dtype=np.float32)
                    det = float(np.linalg.det(rot3))
            except Exception:
                det = None

            # write a compact snapshot
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            lines = []
            lines.append(f"\n=== CAM SNAPSHOT {ts} ===")
            lines.append(f"zoom: {zoom}")
            if center is not None:
                try:
                    lines.append(f"center: {tuple(float(x) for x in center)}")
                except Exception:
                    lines.append(f"center: {center}")
            if det is not None:
                lines.append(f"arcball_rot_det: {det}")

            # dump matrices
            try:
                lines.append("lookat(view):")
                lines.append(str(lookat))
            except Exception:
                pass
            try:
                lines.append("transform(model/arcball):")
                lines.append(str(transform))
            except Exception:
                pass
            try:
                vm = (lookat * transform).astype("f4")
                lines.append("view_model:")
                lines.append(str(vm))
            except Exception:
                pass

            # --- splat visibility stats (CPU) ---
            try:
                cpu = getattr(self, "_mgl_splats15_cpu", None)
                if cpu is not None and np is not None and cpu.shape[0] > 0:
                    view_model = np.array(vm, dtype=np.float32) if "vm" in locals() else None
                    if view_model is None:
                        raise RuntimeError("vm not available")

                    pos = cpu[:, 0:3].astype(np.float32, copy=False)
                    ones = np.ones((pos.shape[0], 1), dtype=np.float32)
                    pos4 = np.concatenate([pos, ones], axis=1)
                    
                    viewp = pos4 @ view_model
                    z = viewp[:, 2]
                    wv = viewp[:, 3]

                    z_gt0 = int(np.count_nonzero(z > 0.0))
                    w_bad = int(np.count_nonzero(wv <= 0.0))

                    lines.append(f"splats_count: {int(cpu.shape[0])}")
                    lines.append(f"view_z min/max: {float(z.min())} / {float(z.max())}")
                    lines.append(f"view_z > 0 count (behind?): {z_gt0}")
                    lines.append(f"view_w <= 0 count (bad): {w_bad}")
            except Exception as exc:
                lines.append(f"splat_stats_error: {exc}")
            # -------------------------------

            path = getattr(self, "_cam_debug_log_path", None)
            if not path:
                path = Path(tempfile.gettempdir()) / "EchoGraph" / f"echograph_cam_debug_{time.strftime('%Y%m%d')}.log"

            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

            print(f"[CAMLOG] wrote snapshot -> {path}", flush=True)

        except Exception as exc:
            print("[CAMLOG] failed:", exc, flush=True)

    def _build_debug_toggle_button(self) -> None:
        try:
            btn = QtWidgets.QToolButton(self)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setToolTip("Stats")
            btn.setIconSize(QtCore.QSize(14, 14))
            btn.clicked.connect(self._toggle_debug_overlay)
            self._debug_toggle_btn = btn
            self._update_debug_toggle_button()
            btn.show()
        except Exception:
            self._debug_toggle_btn = None

    def _load_debug_toggle_icons(self) -> None:
        if self._debug_toggle_icon_active is not None:
            return
        icon_path = None
        try:
            root = Path(__file__).resolve().parents[2]
            candidate = root / "icons" / "TabIcon.png"
            if candidate.exists():
                icon_path = candidate
        except Exception:
            icon_path = None
        if not icon_path:
            return
        pixmap = QtGui.QPixmap(str(icon_path))
        if pixmap.isNull():
            return
        self._debug_toggle_icon_active = self._tint_toggle_icon(pixmap, 1.15, 0.8)
        self._debug_toggle_icon_inactive = self._tint_toggle_icon(pixmap, 0.65, 0.8)

    def _tint_toggle_icon(self, pixmap: QtGui.QPixmap, brightness: float, opacity: float) -> QtGui.QIcon:
        image = pixmap.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
        w = image.width()
        h = image.height()
        for y in range(h):
            for x in range(w):
                color = image.pixelColor(x, y)
                if color.alpha() == 0:
                    continue
                r = min(255, max(0, int(color.red() * brightness)))
                g = min(255, max(0, int(color.green() * brightness)))
                b = min(255, max(0, int(color.blue() * brightness)))
                a = min(255, max(0, int(color.alpha() * opacity)))
                color.setRed(r)
                color.setGreen(g)
                color.setBlue(b)
                color.setAlpha(a)
                image.setPixelColor(x, y, color)
        return QtGui.QIcon(QtGui.QPixmap.fromImage(image))

    def _update_debug_toggle_button(self) -> None:
        btn = getattr(self, "_debug_toggle_btn", None)
        if btn is None:
            return
        btn.setToolTip("Hide stats" if self._debug_overlay else "Show stats")
        self._load_debug_toggle_icons()
        if self._debug_toggle_icon_active is not None:
            icon = self._debug_toggle_icon_active if self._debug_overlay else self._debug_toggle_icon_inactive
            btn.setIcon(icon)
            btn.setText("")
        else:
            btn.setText("^" if self._debug_overlay else "v")
        if self._debug_overlay:
            bg = "rgba(30,41,59,230)"
        else:
            bg = "rgba(15,23,42,210)"
        btn.setStyleSheet(
            "QToolButton{background:%s;border:1px solid #334155;"
            "color:#e2e8f0;padding:0px;border-radius:4px;font-size:11px;}"
            "QToolButton:hover{background:rgba(51,65,85,230);}"
            % bg
        )

    def _toggle_debug_overlay(self) -> None:
        self._debug_overlay = not self._debug_overlay
        btn = getattr(self, "_debug_copy_btn", None)
        if btn is not None:
            btn.setVisible(self._debug_overlay)
        self._update_debug_toggle_button()
        self.update()

    def _build_debug_copy_button(self) -> None:
        try:
            btn = QtWidgets.QToolButton(self)
            btn.setText("Copy")
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QToolButton{background:rgba(15,23,42,210);border:1px solid #334155;"
                "color:#e2e8f0;padding:2px 6px;border-radius:4px;font-size:10px;}"
                "QToolButton:hover{background:rgba(30,41,59,230);}"
            )
            btn.clicked.connect(self._copy_debug_details)
            self._debug_copy_btn = btn
            btn.setVisible(self._debug_overlay)
        except Exception:
            self._debug_copy_btn = None

    def _copy_debug_details(self) -> None:
        try:
            lines = self._debug_status_lines(include_paths=True)
            QtWidgets.QApplication.clipboard().setText("\n".join(lines))
        except Exception:
            pass

    def _on_model_scale_changed(self, value: int) -> None:
        try:
            scale = max(0.01, float(value) / 100.0)
        except Exception:
            scale = 1.0
        self._model_scale_multiplier = scale
        if hasattr(self, "_model_scale_label"):
            self._model_scale_label.setText(f"Scale {scale:.2f}x")
        self._rebuild_mesh_transforms()
        self._debug_mesh_scale = self._debug_mesh_scale_base * self._model_scale_multiplier
        self._update_quad_vbo()
        if self._auto_frame_on_scale:
            self._reset_camera()
        self.update()

    def _on_frame_clicked(self) -> None:
        if self._use_moderngl:
            self._mgl_frame_camera()
            self.update()
            return
        if self._use_example_pipeline:
            self._frame_example_camera()
            self.update()
            return
        self._reset_camera()
        self.update()

    def _on_snapgrab_clicked(self) -> None:
        paused = False
        try:
            self._render_paused = True
            paused = True

            if hasattr(self, "makeCurrent"):
                self.makeCurrent()

            image = self.grabFramebuffer()
            if image is None or image.isNull():
                return

            # force GPU completion (driver stability)
            try:
                ctx = self.context()
                if ctx is not None:
                    f = ctx.functions()
                    if f is not None and hasattr(f, "glFinish"):
                        f.glFinish()
            except Exception:
                pass

        finally:
            try:
                if hasattr(self, "doneCurrent"):
                    self.doneCurrent()
            except Exception:
                pass
            if paused:
                self._render_paused = False
                self.update()

        start_dir = str(Path.home() / "Pictures")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Snapshot", start_dir, "Images (*.png *.jpg)"
        )
        if not path:
            return
        image.save(path)


    def _on_plane_toggled(self, checked: bool) -> None:
        if not self._render_scene_plane:
            self._show_scene_plane = False
            return
        self._show_scene_plane = bool(checked)
        if not self._show_scene_plane:
            self._scene_texture = None
            self._scene_texture_dirty = False
            self._pending_image = None
        else:
            try:
                self._capture_scene_texture()
            except Exception:
                pass
        self.update()

    def _on_example_scale_changed(self, value: int) -> None:
        if not self._use_example_pipeline:
            return
        scale = max(0.01, float(value) / 100.0)
        self._example_model_scale = scale
        if getattr(self, "_example_scale_label", None) is not None:
            self._example_scale_label.setText(f"Scale {scale:.2f}x")
        self._update_example_transform()
        self.update()
        self.update()
    
    def load_model_path(self, path: str | Path, texture_path: str | Path | None = None, frame: bool = True) -> None:
        if not path:
            return
        try:
            model_path = Path(path)
        except Exception:
            return
        if not model_path.exists():
            return

        texture_str = ""
        if texture_path:
            try:
                texture_str = str(Path(texture_path))
            except Exception:
                texture_str = str(texture_path)

        # store whether we should frame when the model actually applies
        self._manual_model_frame = bool(frame)

        if self._use_moderngl:
            self._mgl_load_mesh(model_path)
            if texture_str:
                self._apply_texture_path(texture_str)

            # auto-frame after loading (prevents zoom=10000 keeping the model offscreen)
            if self._manual_model_frame:
                self._mgl_frame_camera()

            self.update()
            return

        if self._use_example_pipeline:
            self._queue_example_model(model_path)
            self.update()
            return

        self._render_scene_models = True
        self._manual_model_path = model_path
        self._show_scene_plane = False
        if getattr(self, "_plane_toggle", None) is not None:
            try:
                self._plane_toggle.setChecked(False)
            except Exception:
                pass
        self._scene_texture = None
        self._scene_texture_dirty = False
        self._pending_image = None
        self._model_load_pending = True

        #QtCore.QTimer.singleShot(0, self._apply_manual_model)
        #QtCore.QTimer.singleShot(0, lambda: self.gl_view.debug_points())

    def load_scene_assets(self, assets: List[Dict[str, str]], frame: bool = True) -> None:
        if not assets:
            return
        if self._use_moderngl:
            # Reset per-owner xforms so reopen uses saved workflow values
            try:
                self._mgl_scene_xforms_by_owner = {}
            except Exception:
                pass
            try:
                self._mgl_scene_splat_xforms_by_owner = {}
            except Exception:
                pass
            # Reset visibility map from current assets (avoid persisting prior hides)
            try:
                vis_map = {}
                for entry in assets or []:
                    if not isinstance(entry, dict):
                        continue
                    name = (entry.get("node") or "").strip()
                    if not name:
                        path_str = str(entry.get("path", "") or "").strip()
                        if path_str:
                            name = Path(path_str).name
                    if not name:
                        continue
                    vis_map[name] = bool(entry.get("visible", True))
                self._mgl_scene_visibility = vis_map
                try:
                    self._mgl_log(
                        "scene: visibility map owners="
                        + str(len(vis_map))
                        + " any_visible="
                        + str(any(vis_map.values()) if vis_map else False)
                    )
                except Exception:
                    pass
            except Exception:
                pass
            # Seed xforms from saved workflow data (if present on assets)
            try:
                for entry in assets or []:
                    if not isinstance(entry, dict):
                        continue
                    name = (entry.get("node") or "").strip()
                    if not name:
                        path_str = str(entry.get("path", "") or "").strip()
                        if path_str:
                            name = Path(path_str).name
                    if not name:
                        continue
                    xf = entry.get("xform")
                    if not isinstance(xf, dict):
                        continue
                    ext = str(entry.get("ext") or "").lower()
                    if not ext:
                        ext = Path(str(entry.get("path", "") or "")).suffix.lower()
                    if ext == ".ply":
                        self._mgl_scene_splat_xforms_by_owner[name] = dict(xf)
                    else:
                        self._mgl_scene_xforms_by_owner[name] = dict(xf)
            except Exception:
                pass
            try:
                self._mgl_load_scene_assets(assets, frame=frame)
            except Exception:
                pass
            # Clear selection/gizmo on fresh scene load
            self._xform_gizmo_owner = None
            self._xform_gizmo_owner_kind = None
            self._xform_gizmo_pos_locked = False
            self._xform_gizmo_pos = (0.0, 0.0, 0.0)
            # Also clear outliner selection if available
            try:
                w = self.window()
                if w is not None and hasattr(w, "clear_scene_asset_selection"):
                    w.clear_scene_asset_selection()
            except Exception:
                pass
            self.update()
            return

        for asset in assets:
            path = str(asset.get("path", "") or "").strip()
            if not path:
                continue
            ext = Path(path).suffix.lower()
            if ext == ".ply":
                try:
                    from echograph.util.splats_io import load_splats_ply

                    splats = load_splats_ply(path)
                    self.set_splats(splats)
                except Exception:
                    import traceback
                    print("[PLY] load_splats_ply FAILED:\n" + traceback.format_exc(), flush=True)
                if frame:
                    try:
                        self._reset_camera()
                    except Exception:
                        pass
                return
            self.load_model_path(path, asset.get("texture"), frame=frame)
            return

    def set_scene_asset_visible(self, owner: str, visible: bool) -> None:
        key = str(owner or "").strip()
        if not key:
            return
        try:
            self._mgl_scene_visibility[key] = bool(visible)
            if self._use_moderngl:
                # Defer visibility changes to GL thread to avoid crashes.
                try:
                    pending = getattr(self, "_mgl_pending_visibility", None)
                    if not isinstance(pending, dict):
                        pending = {}
                        self._mgl_pending_visibility = pending
                    pending[key] = bool(visible)
                    self._mgl_visibility_dirty = True
                except Exception:
                    pass
                self.update()
                return
        except Exception:
            try:
                import traceback
                self._mgl_log("scene: set_visible exception tb=" + traceback.format_exc().strip())
            except Exception:
                pass
            return

    def rename_scene_asset_owner(self, old_name: str, new_name: str) -> None:
        old_key = str(old_name or "").strip()
        new_key = str(new_name or "").strip()
        if not old_key or not new_key or old_key == new_key:
            return

        # track whether this owner actually had splats
        had_splat = False
        try:
            had_splat = old_key in self._mgl_scene_splats
        except Exception:
            had_splat = False

        # rename visibility entry
        if old_key in self._mgl_scene_visibility:
            self._mgl_scene_visibility[new_key] = self._mgl_scene_visibility.pop(old_key)

        # rename splat storage only if needed
        if had_splat:
            try:
                self._mgl_scene_splats[new_key] = self._mgl_scene_splats.pop(old_key)
            except Exception:
                pass

        if self._use_moderngl:
            # rename any scene items that store the owner name in payload
            try:
                self._mgl_rename_scene_item_owner(old_key, new_key)
            except Exception:
                pass

            # IMPORTANT: only rebuild splats if this rename involved splats
            if had_splat:
                try:
                    self._mgl_rebuild_scene_splats(preserve_camera=True)
                except Exception:
                    pass

            self.update()


    def _default_models_dir(self) -> Optional[Path]:
        try:
            root = Path(__file__).resolve().parents[2]
        except Exception:
            return None
        candidate = root / "echograph" / "3dmodels"
        if candidate.is_dir():
            return candidate
        return None

    def _on_pick_model(self) -> None:
        if not self._render_scene_models:
            return
        if self._model_load_pending:
            return
        start_dir = ""
        root = self._default_models_dir()
        if root is not None:
            start_dir = str(root)
        try:
            parent = self.window()
        except Exception:
            parent = self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Select Model",
            start_dir,
            "3D Models (*.obj *.gltf *.glb *.fbx);;All Files (*.*)",
        )
        if not path:
            return
        try:
            self._manual_model_path = Path(path)
        except Exception:
            return
        self._show_scene_plane = False
        if getattr(self, "_plane_toggle", None) is not None:
            try:
                self._plane_toggle.setChecked(False)
            except Exception:
                pass
        self._scene_texture = None
        self._scene_texture_dirty = False
        self._pending_image = None
        self._model_load_pending = True
        QtCore.QTimer.singleShot(0, self._apply_manual_model)

    def _on_example_pick_model(self) -> None:
        if not self._use_example_pipeline:
            return
        start_dir = ""
        root = self._default_models_dir()
        if root is not None:
            start_dir = str(root)
        try:
            parent = self.window()
        except Exception:
            parent = self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Select Model",
            start_dir,
            "3D Models (*.obj *.gltf *.glb *.fbx);;All Files (*.*)",
        )
        if not path:
            return
        self._queue_example_model(Path(path))

    def _apply_manual_model(self) -> None:
        if not self._render_scene_models:
            return

        self._model_load_pending = False
        self._render_paused = True

        try:
            self._load_scene_models()

            # Only frame/reset if requested
            if bool(getattr(self, "_manual_model_frame", True)):
                self._reset_camera()

        except Exception:
            # Keep it silent like your original
            return

        finally:
            self._render_paused = False
            self.update()


    def _capture_scene_texture(self) -> None:
        if not self._render_scene_plane:
            return
        if not self._show_scene_plane:
            return
        if self._scene is None:
            return
        rect = QtCore.QRectF(self._scene.itemsBoundingRect())
        if rect.isNull():
            rect = QtCore.QRectF(self._scene.sceneRect())
        margin = 80.0
        rect = rect.adjusted(-margin, -margin, margin, margin)
        max_dim = 4096
        scale = 1.0
        if rect.width() > 0 and rect.height() > 0:
            scale = min(1.0, max_dim / rect.width(), max_dim / rect.height())
        img_w = max(1, int(rect.width() * scale))
        img_h = max(1, int(rect.height() * scale))
        if hasattr(QtGui.QImage, "Format_RGBA8888"):
            fmt = QtGui.QImage.Format_RGBA8888
        else:
            fmt = QtGui.QImage.Format_ARGB32
        image = QtGui.QImage(img_w, img_h, fmt)
        bg = QtGui.QColor("#1a1f24")
        try:
            if self._scene is not None:
                brush = self._scene.backgroundBrush()
                if brush.style() != QtCore.Qt.NoBrush:
                    bg = brush.color()
        except Exception:
            pass
        image.fill(bg)
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        if not self._render_scene_with_view(painter, rect, img_w, img_h):
            self._scene.render(
                painter,
                QtCore.QRectF(0, 0, img_w, img_h),
                rect,
                QtCore.Qt.IgnoreAspectRatio,
            )
        painter.end()
        self._scene_src_rect = rect
        self._scene_texture_dirty = True
        self._pending_image = image
        self._scene_content_blank = self._estimate_scene_blank(image, bg)

    def _render_scene_with_view(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        img_w: int,
        img_h: int,
    ) -> bool:
        view = self._ensure_capture_view()
        if view is None:
            return False
        try:
            view.setScene(self._scene)
            view.setSceneRect(rect)
            view.resetTransform()
            view.setFixedSize(img_w, img_h)
            view.fitInView(rect, QtCore.Qt.KeepAspectRatio)
            source = QtCore.QRectF(view.viewport().rect())
            target = QtCore.QRectF(0.0, 0.0, img_w, img_h)
            view.render(painter, target, source, QtCore.Qt.IgnoreAspectRatio)
            return True
        except Exception:
            return False

    def _ensure_capture_view(self) -> QtWidgets.QGraphicsView | None:
        if self._capture_view is not None:
            return self._capture_view
        try:
            view = QtWidgets.QGraphicsView(self._scene)
            view.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
            view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            view.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            view.setFrameStyle(QtWidgets.QFrame.NoFrame)
            view.setRenderHint(QtGui.QPainter.Antialiasing, True)
            view.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
            view.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            try:
                view.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
            except Exception:
                pass
            self._capture_view = view
            return view
        except Exception:
            return None

    def _choose_grid_spacing(self, extent: float) -> float:
        target = max(1.0, extent / 10.0)
        candidates = [25.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0]
        best = candidates[0]
        best_delta = abs(best - target)
        for cand in candidates[1:]:
            delta = abs(cand - target)
            if delta < best_delta:
                best = cand
                best_delta = delta
        return best

    def _update_grid(self, extent: float) -> None:
        extent = max(200.0, float(extent))
        spacing = self._choose_grid_spacing(extent)
        count = int(max(1, extent / spacing))
        count = min(count, 200)
        extent = count * spacing
        verts: List[float] = []
        for i in range(-count, count + 1):
            x = i * spacing
            verts.extend([x, -extent, self._grid_z, x, extent, self._grid_z])
            y = i * spacing
            verts.extend([-extent, y, self._grid_z, extent, y, self._grid_z])
        self._grid_vertices = verts
        self._grid_count = len(verts) // 3
        self._grid_dirty = True

    def _upload_grid(self) -> None:
        if not self._grid_dirty:
            return
        if QOpenGLBuffer is None or not self._grid_vertices:
            return
        if self._grid_vbo is None:
            self._grid_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._grid_vbo.create()
        if not self._grid_vbo.bind():
            return
        data = QtCore.QByteArray(struct.pack(f"{len(self._grid_vertices)}f", *self._grid_vertices))
        self._grid_vbo.allocate(data, data.size())
        self._grid_vbo.release()
        self._grid_dirty = False

    def _example_cube_data(self) -> Tuple[List[float], List[int]]:
        colors = [
            (0.94, 0.41, 0.41, 1.0),
            (0.40, 0.84, 0.47, 1.0),
            (0.39, 0.56, 0.92, 1.0),
            (0.96, 0.78, 0.33, 1.0),
            (0.34, 0.87, 0.84, 1.0),
            (0.86, 0.47, 0.92, 1.0),
        ]
        faces = [
            [(-1.0, -1.0,  1.0), (1.0, -1.0,  1.0), (-1.0,  1.0,  1.0), (1.0,  1.0,  1.0)],
            [(1.0, -1.0,  1.0), (1.0, -1.0, -1.0), (1.0,  1.0,  1.0), (1.0,  1.0, -1.0)],
            [(1.0, -1.0, -1.0), (-1.0, -1.0, -1.0), (1.0,  1.0, -1.0), (-1.0,  1.0, -1.0)],
            [(-1.0, -1.0, -1.0), (-1.0, -1.0,  1.0), (-1.0,  1.0, -1.0), (-1.0,  1.0,  1.0)],
            [(-1.0, -1.0, -1.0), (1.0, -1.0, -1.0), (-1.0, -1.0,  1.0), (1.0, -1.0,  1.0)],
            [(-1.0,  1.0,  1.0), (1.0,  1.0,  1.0), (-1.0,  1.0, -1.0), (1.0,  1.0, -1.0)],
        ]
        vertices: List[float] = []
        for face_index, face in enumerate(faces):
            color = colors[face_index % len(colors)]
            a, b, c, d = face
            for x, y, z in (a, b, c):
                vertices.extend([x, y, z, color[0], color[1], color[2], color[3]])
            for x, y, z in (c, b, d):
                vertices.extend([x, y, z, color[0], color[1], color[2], color[3]])
        return vertices, []

    def _example_grid_data(self, extent: float = 12.0, step: float = 1.0) -> List[float]:
        color = (0.36, 0.42, 0.52, 0.65)
        step = max(0.1, float(step))
        count = max(1, int(extent / step))
        size = count * step
        verts: List[float] = []
        for i in range(-count, count + 1):
            x = i * step
            verts.extend([x, 0.0, -size, color[0], color[1], color[2], color[3]])
            verts.extend([x, 0.0,  size, color[0], color[1], color[2], color[3]])
            z = i * step
            verts.extend([-size, 0.0, z, color[0], color[1], color[2], color[3]])
            verts.extend([ size, 0.0, z, color[0], color[1], color[2], color[3]])
        return verts

    def _upload_example_grid(self, vertices: List[float]) -> bool:
        if QOpenGLBuffer is None:
            return False
        if not vertices:
            return False
        if self._example_grid_vbo is None:
            self._example_grid_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._example_grid_vbo.create()
        if not self._example_grid_vbo.bind():
            return False
        try:
            data = self._float_bytes(vertices)
        except Exception:
            self._example_grid_vbo.release()
            return False
        self._example_grid_vbo.allocate(data, data.size())
        self._example_grid_vbo.release()
        self._example_grid_count = len(vertices) // 7
        return True

    def _build_example_program(
        self,
        vertex_src: str,
        fragment_src: str,
        bind_locations: Optional[Dict[str, int]] = None,
    ) -> Optional[QtGui.QOpenGLShaderProgram]:
        program, err = build_qt_program(
            QOpenGLShaderProgram,
            QOpenGLShader,
            vertex_src,
            fragment_src,
            bind_locations,
        )
        if program is None:
            self._shader_error = err
            return None
        return program

    def _float_bytes(self, values: List[float]) -> QtCore.QByteArray:
        return float_bytes(QtCore, array, values)

    def _upload_example_vertices(self, vertices: List[float]) -> bool:
        if QOpenGLBuffer is None:
            self._shader_error = "OpenGL buffers unavailable"
            return False
        if not vertices:
            self._shader_error = "No vertices to upload"
            return False
        if self._example_vbo is None:
            self._example_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._example_vbo.create()
        if not self._example_vbo.bind():
            self._shader_error = "Vertex buffer bind failed"
            return False
        try:
            data = self._float_bytes(vertices)
        except Exception:
            self._shader_error = "Vertex buffer build failed"
            self._example_vbo.release()
            return False
        self._example_vbo.allocate(data, data.size())
        self._example_vbo.release()
        self._example_draw_count = len(vertices) // 7
        self._example_index_count = 0
        return True

    def _init_example_pipeline(self) -> None:
        if self._gl is None:
            self._shader_error = "GL functions unavailable"
            return
        if QOpenGLShaderProgram is None or QOpenGLShader is None:
            self._shader_error = "Shaders unavailable"
            return
        try:
            self._gl.glEnable(GL_DEPTH_TEST)
            self._gl.glClearColor(0.8, 0.8, 0.8, 1.0)
        except Exception:
            self._shader_error = "OpenGL state init failed"
            return
        if QOpenGLVertexArrayObject is not None:
            self._example_vao = QOpenGLVertexArrayObject()
            try:
                self._example_vao.create()
            except Exception:
                self._example_vao = None

        # Example Vertex Debug Shader
        self._shader_error = ""
        vertex_330 = SHADERS["example_vertex_330"]
        fragment_330 = SHADERS["example_fragment_330"]
        vertex_legacy = SHADERS["example_vertex_legacy"]
        fragment_legacy = SHADERS["example_fragment_legacy"]

        bind_locations = {"a_position": 0, "a_color": 1}
        program = self._build_example_program(vertex_330, fragment_330, bind_locations)
        if program is None:
            program = self._build_example_program(vertex_legacy, fragment_legacy, bind_locations)
        if program is None:
            if not self._shader_error:
                self._shader_error = "Shader compile failed"
            return
        self._shader_error = ""
        self._example_program = program

        vertices, _ = self._example_cube_data()
        if not self._upload_example_vertices(vertices):
            return
        grid_vertices = self._example_grid_data(self._example_grid_extent, 1.0)
        self._upload_example_grid(grid_vertices)
        self._example_model_center = (0.0, 0.0, 0.0)
        self._example_model_base_scale = 1.0
        self._example_model_scale = 1.0
        self._update_example_transform()
        self._update_example_projection()

    def _queue_example_model(self, path: Path) -> None:
        try:
            model_data = load_model(path)
        except Exception:
            model_data = None
        if not model_data or not model_data.vertices:
            self._shader_error = "Model load failed"
            return
        color = (0.56, 0.86, 0.98, 1.0)
        vertices: List[float] = []
        src = model_data.vertices
        for i in range(0, len(src), 3):
            vertices.extend([src[i], src[i + 1], src[i + 2], color[0], color[1], color[2], color[3]])
        self._example_pending_vertices = vertices
        self._example_pending_bounds = model_data.bounds
        self._example_model_path = str(path)
        self._example_pending_count = len(vertices) // 7
        self.update()

    def _apply_example_bounds(self, bounds: Tuple[float, float, float, float, float, float]) -> None:
        min_x, min_y, min_z, max_x, max_y, max_z = bounds
        cx = (min_x + max_x) * 0.5
        cy = (min_y + max_y) * 0.5
        cz = (min_z + max_z) * 0.5
        extent = max(max_x - min_x, max_y - min_y, max_z - min_z, 1.0)
        self._example_model_center = (cx, cy, cz)
        self._example_model_base_scale = 18.0 / extent
        self._example_model_extent = extent
        scaled_extent = self._example_scaled_extent()
        self._example_grid_extent = max(6.0, scaled_extent * 1.2)
        step = max(0.5, self._example_grid_extent / 20.0)
        self._upload_example_grid(self._example_grid_data(self._example_grid_extent, step))
        self._update_example_transform()
        self._update_example_projection()

    def _example_scaled_extent(self) -> float:
        extent = max(1.0, float(self._example_model_extent))
        scale = max(1e-4, float(self._example_model_base_scale * self._example_model_scale))
        return max(0.1, extent * scale)

    def _update_example_transform(self) -> None:
        if not self._use_example_pipeline:
            return
        cx, cy, cz = self._example_model_center
        scale = self._example_model_base_scale * self._example_model_scale
        self._example_transform.setToIdentity()
        self._example_transform.scale(scale, scale, scale)
        self._example_transform.translate(-cx, -cy, -cz)
        self._update_example_projection()

    def _frame_example_camera(self) -> None:
        center = QtGui.QVector3D(*self._example_model_center)
        extent = self._example_scaled_extent()
        direction = QtGui.QVector3D(1.0, 1.0, 1.0)
        direction.normalize()
        distance = max(12.0, extent * 2.5)
        self._example_cam_look = center
        self._example_cam_pos = center + direction * distance
        self._update_example_projection()

    def _apply_example_pending(self) -> None:
        if self._example_pending_vertices is None:
            return
        vertices = self._example_pending_vertices
        if not self._upload_example_vertices(vertices):
            return
        bounds = self._example_pending_bounds
        if bounds is not None:
            self._apply_example_bounds(bounds)
            self._frame_example_camera()
        self._example_pending_vertices = None
        self._example_pending_bounds = None
        self._example_pending_count = 0

    def _update_example_projection(self) -> None:
        w = max(1, self.width())
        h = max(1, self.height())
        distance = (self._example_cam_pos - self._example_cam_look).length()
        extent = self._example_scaled_extent()
        near = max(0.02, distance - extent * 4.0)
        far = max(distance + extent * 4.0, near + extent * 8.0)
        self._example_near = near
        self._example_far = far
        self._example_proj.setToIdentity()
        self._example_proj.perspective(self._example_fov, w / float(h), self._example_near, self._example_far)

    def _dbgprint(self, enabled: bool, *a, **k) -> None:
        if enabled:
            print(*a, **k)

    def _camdbg(self, *a) -> None:
        if bool(getattr(self, "_mgl_cam_debug", False)):
            print(*a, flush=True)

    def _update_example_camera_basis(self) -> None:
        direction = self._example_cam_pos - self._example_cam_look
        if direction.lengthSquared() < 1e-6:
            direction = QtGui.QVector3D(0.0, 0.0, 1.0)
        direction.normalize()
        world_up = QtGui.QVector3D(0.0, 1.0, 0.0)
        right = QtGui.QVector3D.crossProduct(world_up, direction)
        if right.lengthSquared() < 1e-6:
            right = QtGui.QVector3D(1.0, 0.0, 0.0)
        right.normalize()
        up = QtGui.QVector3D.crossProduct(direction, right)
        up.normalize()
        self._example_cam_right = right
        self._example_cam_up = up

    def _example_view_matrix(self) -> QtGui.QMatrix4x4:
        self._update_example_camera_basis()
        self._sync_example_gizmo()
        view = QtGui.QMatrix4x4()
        view.lookAt(self._example_cam_pos, self._example_cam_look, self._example_cam_up)
        return view

    def _sync_example_gizmo(self) -> None:
        direction = self._example_cam_pos - self._example_cam_look
        if direction.lengthSquared() < 1e-6:
            return
        dist = direction.length()
        if dist <= 1e-6:
            return
        try:
            self._cam_yaw = math.atan2(direction.x(), direction.z())
            self._cam_pitch = math.asin(direction.y() / dist)
        except Exception:
            return

    def _example_orbit(self, delta: QtCore.QPointF) -> None:
        if delta is None:
            return
        self._update_example_camera_basis()
        axis_up = self._example_cam_up
        axis_right = self._example_cam_right
        yaw_deg = -float(delta.x()) * self._example_rotate_speed
        pitch_deg = -float(delta.y()) * self._example_rotate_speed
        direction = self._example_cam_pos - self._example_cam_look
        if direction.lengthSquared() < 1e-6:
            return
        q_yaw = QtGui.QQuaternion.fromAxisAndAngle(axis_up, yaw_deg)
        q_pitch = QtGui.QQuaternion.fromAxisAndAngle(axis_right, pitch_deg)
        direction = q_yaw.rotatedVector(direction)
        direction = q_pitch.rotatedVector(direction)
        self._example_cam_pos = self._example_cam_look + direction

    def _example_pan(self, delta: QtCore.QPointF) -> None:
        if delta is None:
            return
        self._update_example_camera_basis()
        distance = (self._example_cam_pos - self._example_cam_look).length()
        scale = max(0.1, distance) * self._example_pan_speed
        offset = (-float(delta.x()) * self._example_cam_right + float(delta.y()) * self._example_cam_up) * scale
        self._example_cam_pos += offset
        self._example_cam_look += offset

    def _example_zoom(self, delta_steps: float) -> None:
        if abs(delta_steps) < 1e-6:
            return
        direction = self._example_cam_pos - self._example_cam_look
        dist = direction.length()
        if dist < 1e-6:
            return
        direction.normalize()
        base = 1.0 + 0.12 * self._example_zoom_step
        if base <= 1.0:
            base = 1.05
        zoom = math.pow(base, abs(delta_steps))
        if delta_steps > 0.0:
            dist = dist / zoom
        else:
            dist = dist * zoom
        min_dist = 0.1
        max_dist = max(min_dist * 2.0, self._example_far * 0.95)
        dist = max(min_dist, min(max_dist, dist))
        self._example_cam_pos = self._example_cam_look + direction * dist
        self._update_example_projection()

    def _paint_example(self) -> None:
        if self._example_program is None or self._gl is None:
            return
        if self._shader_error:
            self._gl.glClearColor(0.12, 0.12, 0.12, 1.0)
            self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            return
        try:
            self._gl.glEnable(GL_DEPTH_TEST)
            self._gl.glDepthFunc(GL_LESS)
            self._gl.glDisable(GL_CULL_FACE)
        except Exception:
            pass
        if self._example_draw_count == 0 and self._example_pending_vertices is None:
            vertices, _ = self._example_cube_data()
            self._upload_example_vertices(vertices)
        self._apply_example_pending()
        fov = max(10.0, min(120.0, float(self._example_fov)))
        if abs(fov - self._example_fov) > 0.01:
            self._example_fov = fov
            self._update_example_projection()
        clear = self._example_clear_color
        self._gl.glClearColor(clear.redF(), clear.greenF(), clear.blueF(), 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        view = self._example_view_matrix()
        if self._example_vao is not None:
            try:
                self._example_vao.bind()
            except Exception:
                pass
        self._example_program.bind()
        self._example_program.setUniformValue("u_proj", self._example_proj)
        self._example_program.setUniformValue("u_view", view)

        stride = 7 * 4

        def bind_attributes(vbo: QOpenGLBuffer) -> bool:
            if vbo is None or not vbo.bind():
                return False
            self._example_program.enableAttributeArray(0)
            self._example_program.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
            self._example_program.enableAttributeArray(1)
            self._example_program.setAttributeBuffer(1, GL_FLOAT, 12, 4, stride)
            return True

        if self._example_grid_vbo and self._example_grid_count > 0:
            grid_transform = QtGui.QMatrix4x4()
            self._example_program.setUniformValue("u_trans", grid_transform)
            if bind_attributes(self._example_grid_vbo):
                self._gl.glDrawArrays(GL_LINES, 0, self._example_grid_count)
                self._example_grid_vbo.release()

        if self._example_draw_count > 0 and self._example_vbo is not None:
            self._example_program.setUniformValue("u_trans", self._example_transform)
            if bind_attributes(self._example_vbo):
                self._gl.glDrawArrays(GL_TRIANGLES, 0, self._example_draw_count)
                self._example_vbo.release()
        self._example_program.release()
        if self._example_vao is not None:
            try:
                self._example_vao.release()
            except Exception:
                pass

    def _estimate_scene_blank(self, image: QtGui.QImage, bg: QtGui.QColor) -> bool:
        if image is None or image.isNull():
            return True
        bg_r = bg.red()
        bg_g = bg.green()
        bg_b = bg.blue()
        w = max(1, image.width())
        h = max(1, image.height())
        samples = 0
        for ix in range(0, 5):
            x = int((w - 1) * (ix / 4.0))
            for iy in range(0, 5):
                y = int((h - 1) * (iy / 4.0))
                col = QtGui.QColor(image.pixel(x, y))
                dr = abs(col.red() - bg_r)
                dg = abs(col.green() - bg_g)
                db = abs(col.blue() - bg_b)
                if dr + dg + db > 12:
                    return False
                samples += 1
        return True

    def _load_scene_models(self) -> None:
        self._meshes.clear()
        self._mesh_colors.clear()
        self._mesh_transforms.clear()
        self._mesh_meta.clear()
        if not self._render_scene_models:
            return
        if self._scene is None:
            return
        used_paths = set()
        for name, item in getattr(self._scene, "_node_items", {}).items():
            model = getattr(item, "model", None)
            if model is None:
                continue
            path_val = ""
            for p in (model.params or []):
                if (p.get("name") or "").strip().lower() == "path":
                    path_val = (p.get("value") or "").strip()
                    break
            if not path_val:
                continue
            path = Path(path_val)
            if not path.is_file():
                continue
            model_data = load_model(path)
            if not model_data or not model_data.vertices:
                continue
            used_paths.add(str(path))
            tx, ty = 0.0, 0.0
            tz = 0.0
            try:
                tx, ty = model.pos_xy
            except Exception:
                pass
            try:
                tz = float(getattr(model, "pos_z", 0.0))
            except Exception:
                tz = 0.0
            mesh = _GLMesh(model_data.vertices)
            self._meshes[name] = mesh
            self._mesh_colors[name] = QtGui.QColor("#60a5fa")
            model_mat = QtGui.QMatrix4x4()
            min_x, min_y, min_z, max_x, max_y, max_z = model_data.bounds
            cx = (min_x + max_x) * 0.5
            cy = (min_y + max_y) * 0.5
            cz = (min_z + max_z) * 0.5
            extent = max(max_x - min_x, max_y - min_y, max_z - min_z, 1.0)
            base_scale = 18.0 / extent
            scale = base_scale * self._model_scale_multiplier
            model_mat.translate(float(tx), float(ty), float(tz))
            model_mat.scale(scale, scale, scale)
            model_mat.translate(-cx, -cy, -cz)
            self._mesh_transforms[name] = model_mat
            self._mesh_meta[name] = {
                "center": (cx, cy, cz),
                "bounds": model_data.bounds,
                "pos": (float(tx), float(ty), float(tz)),
                "base_scale": base_scale,
                "path": str(path),
            }

        manual_path = self._manual_model_path
        if manual_path is not None and manual_path.is_file():
            if str(manual_path) not in used_paths:
                model_data = load_model(manual_path)
                if model_data and model_data.vertices:
                    mesh = _GLMesh(model_data.vertices)
                    self._meshes["__manual__"] = mesh
                    self._mesh_colors["__manual__"] = QtGui.QColor("#f59e0b")
                    min_x, min_y, min_z, max_x, max_y, max_z = model_data.bounds
                    cx = (min_x + max_x) * 0.5
                    cy = (min_y + max_y) * 0.5
                    cz = (min_z + max_z) * 0.5
                    extent = max(max_x - min_x, max_y - min_y, max_z - min_z, 1.0)
                    base_scale = 18.0 / extent
                    scale = base_scale * self._model_scale_multiplier
                    model_mat = QtGui.QMatrix4x4()
                    model_mat.translate(0.0, 0.0, 0.0)
                    model_mat.scale(scale, scale, scale)
                    model_mat.translate(-cx, -cy, -cz)
                    self._mesh_transforms["__manual__"] = model_mat
                    self._mesh_meta["__manual__"] = {
                        "center": (cx, cy, cz),
                        "bounds": model_data.bounds,
                        "pos": (0.0, 0.0, 0.0),
                        "base_scale": base_scale,
                        "path": str(manual_path),
                    }

    def _rebuild_mesh_transforms(self) -> None:
        if not self._mesh_meta:
            return
        self._mesh_transforms.clear()
        for name, meta in self._mesh_meta.items():
            cx, cy, cz = meta.get("center", (0.0, 0.0, 0.0))
            tx, ty, tz = meta.get("pos", (0.0, 0.0, 0.0))
            base_scale = float(meta.get("base_scale", 1.0))
            scale = base_scale * self._model_scale_multiplier
            model_mat = QtGui.QMatrix4x4()
            model_mat.translate(float(tx), float(ty), float(tz))
            model_mat.scale(scale, scale, scale)
            model_mat.translate(-cx, -cy, -cz)
            self._mesh_transforms[name] = model_mat

    def _mesh_bounds_world(self) -> Optional[Tuple[float, float, float, float, float, float]]:
        if not self._mesh_meta:
            return None
        min_x = min_y = min_z = float("inf")
        max_x = max_y = max_z = float("-inf")
        for meta in self._mesh_meta.values():
            bounds = meta.get("bounds")
            if not bounds:
                continue
            cx, cy, cz = meta.get("center", (0.0, 0.0, 0.0))
            tx, ty, tz = meta.get("pos", (0.0, 0.0, 0.0))
            base_scale = float(meta.get("base_scale", 1.0))
            scale = base_scale * self._model_scale_multiplier
            min_x = min(min_x, tx + (bounds[0] - cx) * scale)
            max_x = max(max_x, tx + (bounds[3] - cx) * scale)
            min_y = min(min_y, ty + (bounds[1] - cy) * scale)
            max_y = max(max_y, ty + (bounds[4] - cy) * scale)
            min_z = min(min_z, tz + (bounds[2] - cz) * scale)
            max_z = max(max_z, tz + (bounds[5] - cz) * scale)
        if min_x == float("inf"):
            return None
        return min_x, min_y, min_z, max_x, max_y, max_z

    def initializeGL(self) -> None:
        ctx = self.context()
        if ctx is not None:
            self._gl = ctx.functions()
            try:
                self._gl.initializeOpenGLFunctions()
            except Exception:
                pass

        if self._use_moderngl:
            self._init_mgl_renderer()
            return
        if self._use_example_pipeline:
            self._init_example_pipeline()
            return
        if QOpenGLShaderProgram is None:
            return
        self._gl.glEnable(GL_BLEND)
        self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self._gl.glEnable(GL_DEPTH_TEST)
        try:
            self._gl.glLineWidth(1.0)
        except Exception:
            pass

        if QOpenGLVertexArrayObject is not None:
            self._vao = QOpenGLVertexArrayObject()
            try:
                self._vao.create()
            except Exception:
                self._vao = None

        # Quad program
        self._quad_program, err = build_qt_program(
            QOpenGLShaderProgram,
            QOpenGLShader,
            SHADERS["quad_vert"],
            SHADERS["quad_frag"],
            bind_locations={"a_pos": 0, "a_uv": 1},
        )
        if self._quad_program is None:
            self._shader_error = err
            return

        self._quad_pos_loc = self._quad_program.attributeLocation("a_pos")
        self._quad_uv_loc = self._quad_program.attributeLocation("a_uv")

        # Mesh program
        self._mesh_program, err = build_qt_program(
            QOpenGLShaderProgram,
            QOpenGLShader,
            SHADERS["mesh_vert"],
            SHADERS["mesh_frag"],
            bind_locations={"a_pos": 0},
        )
        if self._mesh_program is None:
            self._shader_error = (self._shader_error + "\n" + err).strip()
            return

        self._mesh_pos_loc = self._mesh_program.attributeLocation("a_pos")

        if QOpenGLBuffer is not None:
            self._quad_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self._quad_vbo.create()
        self._debug_mesh = _GLMesh(debug_cube_vertices())
        self._debug_wire = _GLMesh(debug_cube_wire_vertices())

        self._upload_scene_texture()
        for mesh in self._meshes.values():
            mesh.upload()
        if self._debug_mesh is not None:
            self._debug_mesh.upload()
        if self._debug_wire is not None:
            self._debug_wire.upload()

    def resizeGL(self, w: int, h: int) -> None:
        if hasattr(self, "_gl"):
            self._gl.glViewport(0, 0, w, h)
        if self._use_moderngl and self._mgl_ctx is not None:
            self._mgl_ctx.viewport = (0, 0, max(2, w), max(2, h))
            if self._mgl_arcball is not None:
                self._mgl_arcball.setBounds(w, h)
        if self._use_example_pipeline:
            self._update_example_projection()

    def _upload_scene_texture(self) -> None:
        if not getattr(self, "_scene_texture_dirty", False):
            return
        image = getattr(self, "_pending_image", None)
        if image is None:
            return

        self._scene_texture, self._mipmaps_enabled = upload_scene_texture(
            QtGui,
            QOpenGLTexture,
            image,
            getattr(self, "_scene_texture", None),
        )

        self._scene_texture_dirty = False
        self._update_quad_vbo()

    def _update_quad_vbo(self) -> None:
        self._quad_ready = update_quad_vbo(
            QtCore,
            struct,
            getattr(self, "_quad_vbo", None),
            getattr(self, "_scene_src_rect", None),
        )

    def _projection_matrix(self) -> QtGui.QMatrix4x4:
        w = max(1, self.width())
        h = max(1, self.height())
        proj = QtGui.QMatrix4x4()
        if self._use_ortho:
            aspect = w / float(h)
            half = max(self._cube_size * 2.5, float(self._cam_dist))
            if aspect >= 1.0:
                left = -half * aspect
                right = half * aspect
                bottom = -half
                top = half
            else:
                left = -half
                right = half
                bottom = -half / max(1e-6, aspect)
                top = half / max(1e-6, aspect)
            far_plane = max(self._world_extent * 20.0, float(self._cam_dist) * 10.0, 200.0)
            proj.ortho(left, right, bottom, top, 0.001, far_plane)
        else:
            far_plane = max(self._world_extent * 20.0, float(self._cam_dist) * 10.0, 200.0)
            proj.perspective(float(self._fov_deg), w / float(h), 0.1, far_plane)
        return proj

    def _view_matrix(self) -> QtGui.QMatrix4x4:
        target = self._cam_target
        cam_dist = self._cam_dist
        yaw = self._cam_yaw
        pitch = self._cam_pitch
        if (
            self._use_ortho
            and self._show_test_cube
            and self._test_cam_locked
            and not self._render_scene_models
            and not self._render_scene_plane
        ):
            target = QtCore.QPointF(0.0, 0.0)
            cam_dist = max(cam_dist, self._cube_size * 8.0)
            yaw = 0.785398
            pitch = -0.61548
        cy = math.cos(yaw)
        sy = math.sin(yaw)
        cp = math.cos(pitch)
        sp = math.sin(pitch)
        cam_x = target.x() + cam_dist * cp * sy
        cam_y = target.y() + cam_dist * sp
        cam_z = cam_dist * cp * cy
        m = QtGui.QMatrix4x4()
        m.lookAt(
            QtGui.QVector3D(cam_x, cam_y, cam_z),
            QtGui.QVector3D(target.x(), target.y(), 0.0),
            QtGui.QVector3D(0.0, -1.0, 0.0),
        )
        return m

    def paintGL(self) -> None:
        #print("[PAINT] mgl=", self._use_moderngl, "example=", self._use_example_pipeline, "dbg=", getattr(self, "_debug_show_axis_overlay", None))
        if not hasattr(self, "_gl"):
            return

        if self._render_paused:
            bg = getattr(self, "_viewport_bg", QtGui.QColor("#1a1f24"))
            self._gl.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
            self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            return
        #print("[PAINT] use_mgl=", self._use_moderngl, "has_mgl=", _HAS_MGL)
        # ModernGL path (keep clean)
        if self._use_moderngl:
            self._paint_mgl()

            # Make sure we're drawing into the visible Qt FBO
            try:
                self._mgl_bind_default_fbo()
            except Exception:
                pass

            # Axis overlay (debug) in ModernGL path
            if getattr(self, "_debug_show_axis_overlay", False):
                try:
                    if getattr(self, "_axis_overlay", None) is None:
                        from echograph.ui.axis_gizmo_overlay import AxisGizmoOverlay
                        self._axis_overlay = AxisGizmoOverlay()

                    # Only draw gizmo when something is selected (unless idle gizmo is allowed)
                    if getattr(self, "_xform_gizmo_owner", None) is None and not bool(
                        getattr(self, "_xform_gizmo_idle_visible", False)
                    ):
                        return

                    if self._axis_overlay.ensure_gl(self):
                        renderer = getattr(self, "_mgl_renderer", None) or self

                        P = getattr(renderer, "_mgl_pick_proj", None)
                        V = getattr(renderer, "_mgl_pick_view", None)
                        M = getattr(renderer, "_mgl_pick_model", None)

                        if P is None or V is None or M is None:
                            return  # renderer hasn't produced matrices yet

                        pos = getattr(self, "_xform_gizmo_pos", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)

                        import numpy as np
                        T = np.eye(4, dtype=np.float32)
                        T[0, 3] = float(pos[0])
                        T[1, 3] = float(pos[1])
                        T[2, 3] = float(pos[2])

                        mode = getattr(self, "_xform_gizmo_mode", "translate") or "translate"
                        use_rot = (mode == "rotate")
                        R = None
                        if use_rot:
                            try:
                                owner = getattr(self, "_xform_gizmo_owner", None)
                                if owner:
                                    is_splat = False
                                    try:
                                        splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
                                        if not isinstance(splat_map, dict) or not splat_map:
                                            splat_map = getattr(renderer, "_mgl_scene_splats", None)
                                        if isinstance(splat_map, dict) and owner in splat_map:
                                            is_splat = True
                                    except Exception:
                                        is_splat = False
                                    get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                                    xf = get_xf(owner) if callable(get_xf) else {}
                                    rot = tuple((xf or {}).get("rot", (0.0, 0.0, 0.0)))
                                    rx, ry, rz = float(rot[0]), float(rot[1]), float(rot[2])
                                    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
                                    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
                                    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
                                    Rx = np.array(
                                        [[1.0, 0.0, 0.0, 0.0],
                                         [0.0,  cx,  sx, 0.0],
                                         [0.0, -sx,  cx, 0.0],
                                         [0.0, 0.0, 0.0, 1.0]],
                                        dtype=np.float32,
                                    )
                                    Ry = np.array(
                                        [[ cy, 0.0, -sy, 0.0],
                                         [0.0, 1.0, 0.0, 0.0],
                                         [ sy, 0.0,  cy, 0.0],
                                         [0.0, 0.0, 0.0, 1.0]],
                                        dtype=np.float32,
                                    )
                                    Rz = np.array(
                                        [[ cz,  sz, 0.0, 0.0],
                                         [-sz,  cz, 0.0, 0.0],
                                         [0.0, 0.0, 1.0, 0.0],
                                         [0.0, 0.0, 0.0, 1.0]],
                                        dtype=np.float32,
                                    )
                                    # match renderer order: Rz @ Ry @ Rx
                                    R = (Rz @ Ry @ Rx).astype(np.float32)
                            except Exception:
                                R = None

                        TR = (T @ R) if (use_rot and R is not None) else T
                        mvp_np = (P @ V @ M @ TR).astype(np.float32)

                        mvp = QtGui.QMatrix4x4(
                            float(mvp_np[0, 0]), float(mvp_np[0, 1]), float(mvp_np[0, 2]), float(mvp_np[0, 3]),
                            float(mvp_np[1, 0]), float(mvp_np[1, 1]), float(mvp_np[1, 2]), float(mvp_np[1, 3]),
                            float(mvp_np[2, 0]), float(mvp_np[2, 1]), float(mvp_np[2, 2]), float(mvp_np[2, 3]),
                            float(mvp_np[3, 0]), float(mvp_np[3, 1]), float(mvp_np[3, 2]), float(mvp_np[3, 3]),
                        )

                        self._axis_overlay.draw(mvp, mode=mode, draw_rotate_rings=(mode != "rotate"))
                except Exception as exc:
                    print("[AXIS_OVERLAY] disabled:", exc, flush=True)
                    self._debug_show_axis_overlay = False


            return

        if self._use_example_pipeline:
            self._paint_example()
            return

        # Classic Qt GL path
        self._upload_scene_texture()
        self._upload_grid()

        bg = getattr(self, "_viewport_bg", QtGui.QColor("#1a1f24"))
        self._gl.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        if QOpenGLShaderProgram is None:
            return

        if self._vao is not None:
            try:
                self._vao.bind()
            except Exception:
                pass

        if self._show_scene_plane and self._scene_texture and self._quad_program and self._quad_ready:
            self._quad_program.bind()
            mvp = proj * view
            self._quad_program.setUniformValue("u_mvp", mvp)
            self._quad_program.setUniformValue("u_tex", 0)
            self._scene_texture.bind(0)
            if self._quad_vbo and self._quad_vbo.bind():
                stride = 5 * 4
                if self._quad_pos_loc >= 0:
                    self._quad_program.enableAttributeArray(self._quad_pos_loc)
                    self._quad_program.setAttributeBuffer(self._quad_pos_loc, GL_FLOAT, 0, 3, stride)
                if self._quad_uv_loc >= 0:
                    self._quad_program.enableAttributeArray(self._quad_uv_loc)
                    self._quad_program.setAttributeBuffer(self._quad_uv_loc, GL_FLOAT, 12, 2, stride)
                self._gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
                self._quad_vbo.release()
            self._scene_texture.release()
            self._quad_program.release()

        if self._mesh_program and self._grid_count > 0 and self._grid_vbo is not None:
            self._mesh_program.bind()
            model = QtGui.QMatrix4x4()
            model.translate(self._grid_center.x(), self._grid_center.y(), 0.0)
            mvp = proj * view * model
            self._mesh_program.setUniformValue("u_mvp", mvp)
            color = self._grid_color
            self._mesh_program.setUniformValue(
                "u_color",
                QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.55),
            )
            depth_disabled = False
            if self._show_test_cube and not self._render_scene_models and not self._render_scene_plane:
                try:
                    self._gl.glDisable(GL_DEPTH_TEST)
                    depth_disabled = True
                except Exception:
                    depth_disabled = False
            if self._grid_vbo.bind():
                if self._mesh_pos_loc >= 0:
                    self._mesh_program.enableAttributeArray(self._mesh_pos_loc)
                    self._mesh_program.setAttributeBuffer(self._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                self._gl.glDrawArrays(GL_LINES, 0, self._grid_count)
                self._grid_vbo.release()
            if depth_disabled:
                try:
                    self._gl.glEnable(GL_DEPTH_TEST)
                except Exception:
                    pass
            self._mesh_program.release()

        if self._mesh_program and self._render_scene_models:
            for name, mesh in self._meshes.items():
                if mesh.count == 0:
                    continue
                if mesh.vbo is None:
                    mesh.upload()
                if mesh.vbo is None:
                    continue
                model = self._mesh_transforms.get(name, QtGui.QMatrix4x4())
                mvp = proj * view * model
                color = self._mesh_colors.get(name, QtGui.QColor("#60a5fa"))
                self._mesh_program.bind()
                self._mesh_program.setUniformValue("u_mvp", mvp)
                self._mesh_program.setUniformValue(
                    "u_color",
                    QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.85),
                )
                if mesh.vbo.bind():
                    if self._mesh_pos_loc >= 0:
                        self._mesh_program.enableAttributeArray(self._mesh_pos_loc)
                        self._mesh_program.setAttributeBuffer(self._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                    self._gl.glDrawArrays(GL_TRIANGLES, 0, mesh.count)
                    mesh.vbo.release()
                self._mesh_program.release()
        if self._mesh_program and self._debug_mesh and self._show_test_cube:
            if self._debug_mesh.vbo is None:
                self._debug_mesh.upload()
            if self._debug_mesh.vbo is not None:
                model = QtGui.QMatrix4x4()
                model.translate(self._debug_mesh_center.x(), self._debug_mesh_center.y(), 0.0)
                model.scale(self._debug_mesh_scale)
                mvp = proj * view * model
                self._mesh_program.bind()
                self._mesh_program.setUniformValue("u_mvp", mvp)
                color = self._debug_mesh_color
                self._mesh_program.setUniformValue(
                    "u_color",
                    QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.9),
                )
                if self._debug_mesh.vbo.bind():
                    if self._mesh_pos_loc >= 0:
                        self._mesh_program.enableAttributeArray(self._mesh_pos_loc)
                        self._mesh_program.setAttributeBuffer(self._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                    self._gl.glDrawArrays(GL_TRIANGLES, 0, self._debug_mesh.count)
                    self._debug_mesh.vbo.release()
                self._mesh_program.release()
        if self._mesh_program and self._debug_wire and self._show_test_cube:
            if self._debug_wire.vbo is None:
                self._debug_wire.upload()
            if self._debug_wire.vbo is not None:
                model = QtGui.QMatrix4x4()
                model.translate(self._debug_mesh_center.x(), self._debug_mesh_center.y(), 0.0)
                model.scale(self._debug_mesh_scale)
                mvp = proj * view * model
                self._mesh_program.bind()
                self._mesh_program.setUniformValue("u_mvp", mvp)
                color = self._debug_wire_color
                self._mesh_program.setUniformValue(
                    "u_color",
                    QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 1.0),
                )
                depth_disabled = False
                try:
                    self._gl.glDisable(GL_DEPTH_TEST)
                    depth_disabled = True
                except Exception:
                    depth_disabled = False
                if self._debug_wire.vbo.bind():
                    if self._mesh_pos_loc >= 0:
                        self._mesh_program.enableAttributeArray(self._mesh_pos_loc)
                        self._mesh_program.setAttributeBuffer(self._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                    self._gl.glDrawArrays(GL_LINES, 0, self._debug_wire.count)
                    self._debug_wire.vbo.release()
                if depth_disabled:
                    try:
                        self._gl.glEnable(GL_DEPTH_TEST)
                    except Exception:
                        pass
                self._mesh_program.release()
        if self._vao is not None:
            try:
                self._vao.release()
            except Exception:
                pass

    def _draw_overlay(self, painter: Optional[QtGui.QPainter] = None) -> None:
        depth_disabled = False
        if hasattr(self, "_gl"):
            try:
                self._gl.glDisable(GL_DEPTH_TEST)
                depth_disabled = True
            except Exception:
                depth_disabled = False
        owns_painter = False
        if painter is None:
            painter = QtGui.QPainter(self)
            owns_painter = True
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        show_debug = self._debug_overlay
        if show_debug:
            lines = self._debug_status_lines()
            if lines:
                metrics = QtGui.QFontMetrics(painter.font())
                if hasattr(metrics, "horizontalAdvance"):
                    text_width = max(metrics.horizontalAdvance(line) for line in lines)
                else:
                    text_width = max(metrics.width(line) for line in lines)
                text_height = len(lines) * metrics.height() + max(0, len(lines) - 1) * 2
                pad = 8
                panel_top = 10.0
                btn = getattr(self, "_debug_copy_btn", None)
                if btn is not None and btn.isVisible():
                    panel_top = btn.geometry().bottom() + 6.0
                panel_w = text_width + pad * 2
                panel_h = text_height + pad * 2
                dpr = 1.0
                try:
                    dpr = float(self.devicePixelRatioF())
                except Exception:
                    dpr = 1.0
                cache_key = (tuple(lines), painter.font().toString(), panel_w, panel_h, dpr)
                if self._debug_overlay_cache is None or self._debug_overlay_cache_key != cache_key:
                    img_w = max(1, int(panel_w * dpr))
                    img_h = max(1, int(panel_h * dpr))
                    image = QtGui.QImage(img_w, img_h, QtGui.QImage.Format_ARGB32_Premultiplied)
                    image.setDevicePixelRatio(dpr)
                    image.fill(QtCore.Qt.transparent)
                    ip = QtGui.QPainter(image)
                    ip.setRenderHint(QtGui.QPainter.Antialiasing, True)
                    ip.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
                    ip.setPen(QtCore.Qt.NoPen)
                    ip.setBrush(QtCore.Qt.NoBrush)
                    ip.drawRoundedRect(QtCore.QRectF(0, 0, panel_w, panel_h), 6, 6)
                    ip.setPen(QtGui.QColor("#e2e8f0"))
                    x = pad
                    y = pad + metrics.ascent()
                    for line in lines:
                        ip.drawText(QtCore.QPointF(x, y), line)
                        y += metrics.height() + 2
                    ip.end()
                    self._debug_overlay_cache = image
                    self._debug_overlay_cache_key = cache_key
                painter.drawImage(QtCore.QPointF(10, panel_top), self._debug_overlay_cache)
        if self._shader_error:
            painter.setPen(QtGui.QColor("#fca5a5"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "3D View: shader error")
        elif self._show_scene_plane and not self._scene_texture:
            painter.setPen(QtGui.QColor("#e2e8f0"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, "3D View: no scene texture")
        if self._use_moderngl and self._mgl_uv_overlay_enabled:
            self._draw_uv_overlay(painter)
        self._draw_axis_gizmo(painter)
        if owns_painter:
            painter.end()
        if depth_disabled:
            try:
                self._gl.glEnable(GL_DEPTH_TEST)
            except Exception:
                pass

    def _draw_rotate_shared_overlay(self) -> None:
        rot_shared = getattr(self, "_rot_shared", None)
        if rot_shared is None:
            return

        # only show in rotate mode
        mode = getattr(self, "_xform_gizmo_mode", "translate") or "translate"
        if mode != "rotate":
            return

        # only draw when something is selected (unless idle gizmo is allowed)
        if getattr(self, "_xform_gizmo_owner", None) is None and not bool(getattr(self, "_xform_gizmo_idle_visible", False)):
            return

        if np is None:
            return

        renderer = getattr(self, "_mgl_renderer", None) or self
        P = getattr(renderer, "_mgl_pick_proj", None)
        V = getattr(renderer, "_mgl_pick_view", None)
        M = getattr(renderer, "_mgl_pick_model", None)
        if P is None or V is None or M is None:
            return

        pos = getattr(self, "_xform_gizmo_pos", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)

        # build MVP like the axis overlay does (rotate mode uses object rot)
        T = np.eye(4, dtype=np.float32)
        T[0, 3] = float(pos[0])
        T[1, 3] = float(pos[1])
        T[2, 3] = float(pos[2])

        # try to match existing rotate-mode orientation
        R = np.eye(4, dtype=np.float32)
        try:
            owner = getattr(self, "_xform_gizmo_owner", None)
            if owner:
                is_splat = False
                try:
                    splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
                    if not isinstance(splat_map, dict) or not splat_map:
                        splat_map = getattr(renderer, "_mgl_scene_splats", None)
                    if isinstance(splat_map, dict) and owner in splat_map:
                        is_splat = True
                except Exception:
                    is_splat = False

                get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                xf = get_xf(owner) if callable(get_xf) else {}
                rot = tuple((xf or {}).get("rot", (0.0, 0.0, 0.0)))

                rx, ry, rz = float(rot[0]), float(rot[1]), float(rot[2])
                cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
                cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
                cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))

                Rx = np.array(
                    [[1.0, 0.0, 0.0, 0.0],
                     [0.0,  cx,  sx, 0.0],
                     [0.0, -sx,  cx, 0.0],
                     [0.0, 0.0, 0.0, 1.0]],
                    dtype=np.float32,
                )
                Ry = np.array(
                    [[ cy, 0.0, -sy, 0.0],
                     [0.0, 1.0, 0.0, 0.0],
                     [ sy, 0.0,  cy, 0.0],
                     [0.0, 0.0, 0.0, 1.0]],
                    dtype=np.float32,
                )
                Rz = np.array(
                    [[ cz,  sz, 0.0, 0.0],
                     [-sz,  cz, 0.0, 0.0],
                     [0.0, 0.0, 1.0, 0.0],
                     [0.0, 0.0, 0.0, 1.0]],
                    dtype=np.float32,
                )
                R = (Rz @ Ry @ Rx).astype(np.float32)
        except Exception:
            pass

        TR = (T @ R).astype(np.float32)
        mvp_np = (P @ V @ M @ TR).astype(np.float32)

        mvp = QtGui.QMatrix4x4(
            float(mvp_np[0, 0]), float(mvp_np[0, 1]), float(mvp_np[0, 2]), float(mvp_np[0, 3]),
            float(mvp_np[1, 0]), float(mvp_np[1, 1]), float(mvp_np[1, 2]), float(mvp_np[1, 3]),
            float(mvp_np[2, 0]), float(mvp_np[2, 1]), float(mvp_np[2, 2]), float(mvp_np[2, 3]),
            float(mvp_np[3, 0]), float(mvp_np[3, 1]), float(mvp_np[3, 2]), float(mvp_np[3, 3]),
        )

        center = rot_shared.project_to_screen(self.width(), self.height(), mvp, QtGui.QVector3D(0.0, 0.0, 0.0))
        if center is None:
            return

        # DEBUG (print once per session when rotate gizmo actually draws)
        if not getattr(self, "_dbg_rot_shared_sizes_once", False):
            self._dbg_rot_shared_sizes_once = True
            try:
                dpr = float(self.devicePixelRatioF())
            except Exception:
                dpr = 1.0
            print(
                "[ROT_SHARED_SIZES]"
                f" dpr={dpr}"
                f" viewport={self.width()}x{self.height()}"
                f" gizmo_radius={rot_shared.gizmo_radius}"
                f" screen_radius_px={rot_shared.gizmo_screen_radius_px}"
                f" ui_scale={rot_shared.gizmo_ui_scale}"
                f" xyz_px={rot_shared.xyz_ring_radius_px()}"
                f" view_px={rot_shared.view_ring_radius_px()}"
            )

        # scale the local gizmo so the projected XYZ ring radius matches a constant pixel radius
        try:
            # Match gizmo_viewport_smoketest.py: use projection focal length + camera-space depth.
            try:
                dpr = float(self.devicePixelRatioF())
            except Exception:
                dpr = 1.0

            vh = float(max(1, self.height())) * dpr

            # Robust: force numpy 4x4 arrays (Matrix44, lists, etc.)
            Pn = np.asarray(P, dtype=np.float32)
            Vn = np.asarray(V, dtype=np.float32)
            Mn = np.asarray(M, dtype=np.float32)

            proj_y = abs(float(Pn[1, 1]))  # cot(fovy/2)
            if proj_y > 1e-6 and float(rot_shared.gizmo_radius) > 1e-6:
                # camera-space position of gizmo origin
                vm = (Vn @ Mn @ (T @ R)).astype(np.float32)
                cp = vm @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                w = float(cp[3]) if abs(float(cp[3])) > 1e-6 else 1.0

                dist_raw = abs(float(cp[2]) / w)
                dist_raw = max(dist_raw, 0.05)

                try:
                    sm = float(getattr(renderer, "_mgl_scale_multiplier", 1.0))
                except Exception:
                    sm = 1.0

                dist = dist_raw * sm

                # world units per pixel at that depth
                world_per_px = (1.55 * dist) / (vh * proj_y)
                # IMPORTANT: match smoketest target size (XYZ ring radius)
                target_ring_px = float(rot_shared.xyz_ring_radius_px())
                desired_world_radius = target_ring_px * world_per_px

                s = desired_world_radius / float(rot_shared.gizmo_radius)
                s = max(0.01, min(1000.0, float(s)))

                # (if you have clamps, keep them here, then print after clamps)
                # s = max(0.01, min(1000.0, float(s)))

                if not getattr(self, "_dbg_rot_shared_scale_once", False):
                    self._dbg_rot_shared_scale_once = True
                    print(
                        "[ROT_SHARED_SCALE]"
                        f" target_px={target_ring_px}"
                        f" dist={dist}"
                        f" proj_y={proj_y}"
                        f" vh={vh}"
                        f" s={s}"
                    )

                S = np.eye(4, dtype=np.float32)
                S[0, 0] = s
                S[1, 1] = s
                S[2, 2] = s

                TRS = (T @ R @ S).astype(np.float32)
                mvp_np = (Pn @ Vn @ Mn @ TRS).astype(np.float32)

                mvp = QtGui.QMatrix4x4(
                    float(mvp_np[0, 0]), float(mvp_np[0, 1]), float(mvp_np[0, 2]), float(mvp_np[0, 3]),
                    float(mvp_np[1, 0]), float(mvp_np[1, 1]), float(mvp_np[1, 2]), float(mvp_np[1, 3]),
                    float(mvp_np[2, 0]), float(mvp_np[2, 1]), float(mvp_np[2, 2]), float(mvp_np[2, 3]),
                    float(mvp_np[3, 0]), float(mvp_np[3, 1]), float(mvp_np[3, 2]), float(mvp_np[3, 3]),
                )
        except Exception:
            pass

        # cache AFTER scaling so pick matches draw
        self._rot_shared_center_px = center
        self._rot_shared_mvp = mvp
        self._rot_shared_world_pos = pos

        # pick up whatever we cached earlier for picking, otherwise fall back
        view_dir_local = getattr(self, "_rot_shared_view_dir_local", None)
        if view_dir_local is None:
            view_dir_local = QtGui.QVector3D(0.0, 0.0, 1.0)

        # XYZ rings (new shared gizmo)
        rot_shared.draw_xyz_core_2d(
            widget=self,
            viewport_w=self.width(),
            viewport_h=self.height(),
            mvp=mvp,
            view_dir_local=view_dir_local,
            back_clip_cos=-0.2,
            clip_enabled=False,
            width_px=2,
        )

        # draw center + view ring on top (always constant px)
        target_ring_px = float(rot_shared.xyz_ring_radius_px())  # match the big blue ring
        # Hover picking (same usage pattern as gizmo_viewport_smoketest.py)
        mouse_px = getattr(self, "_rot_shared_mouse_px", None)
        hit = None

        if mouse_px is not None and (not rot_shared.drag_axis.active):
            band = max(12.0, float(rot_shared.xyz_ring_radius_px()) * 0.14)
            hit = rot_shared.pick_axis_2d(
                widget=self,
                center=center,
                mouse_px=mouse_px,
                viewport_w=self.width(),
                viewport_h=self.height(),
                mvp=mvp,
                view_dir_local=view_dir_local,
                back_clip_cos=-0.25,
                clip_enabled=False,
                threshold_px=float(band),
            )

        # Resolve hover state
        if rot_shared.drag_axis.active and rot_shared.drag_axis.axis:
            hover_axis = str(rot_shared.drag_axis.axis)
            hover_view = False
        else:
            hover_axis = hit if hit in ("x", "y", "z") else None
            hover_view = bool(hit == "view")

        rot_shared.hover_axis = hover_axis
        rot_shared.hover_view_ring = bool(hover_view)

        # Center disc hover zone (only when not on rings)
        hover_center = False
        if mouse_px is not None and (hover_axis is None) and (not hover_view):
            mx = float(mouse_px.x())
            my = float(mouse_px.y())
            cx = float(center.x())
            cy = float(center.y())
            d = ((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5
            center_grab_r_px = max(10.0, float(rot_shared.xyz_ring_radius_px()) * 0.28)
            hover_center = (d <= float(center_grab_r_px))

        # Draw center + view ring
        target_ring_px = float(rot_shared.xyz_ring_radius_px())
        rot_shared.draw_center_disc_2d(widget=self, center=center, radius_px=target_ring_px, hovered=bool(hover_center))
        rot_shared.draw_view_ring_2d(widget=self, center=center, hovered=bool(hover_view or rot_shared.drag_view))

        # Draw halo on hovered axis (or active drag axis)
        if hover_axis:
            rot_shared.draw_hover_halo_2d(
                widget=self,
                axis=str(hover_axis),
                viewport_w=self.width(),
                viewport_h=self.height(),
                mvp=mvp,
                view_dir_local=view_dir_local,
                back_clip_cos=-0.25,
                clip_enabled=False,
            )

    def _get_owner_rot_deg(self, owner: str):
        renderer = getattr(self, "_mgl_renderer", None) or self
        # detect splat
        is_splat = False
        try:
            splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
            if not isinstance(splat_map, dict) or not splat_map:
                splat_map = getattr(renderer, "_mgl_scene_splats", None)
            if isinstance(splat_map, dict) and owner in splat_map:
                is_splat = True
        except Exception:
            is_splat = False

        get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
        xf = get_xf(owner) if callable(get_xf) else {}
        rot = tuple((xf or {}).get("rot", (0.0, 0.0, 0.0)))
        return (float(rot[0]), float(rot[1]), float(rot[2])), is_splat

    def _set_owner_rot_deg(self, owner: str, rot_deg, is_splat: bool) -> None:
        # Use the existing API that gl_view already uses for transforms.
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            fn = getattr(renderer, "_mgl_set_scene_asset_xform", None)
            if callable(fn):
                fn(
                    owner,
                    rot=(float(rot_deg[0]), float(rot_deg[1]), float(rot_deg[2])),
                    apply_to_scene_models=not bool(is_splat),
                    use_splat_xform=bool(is_splat),
                )
            # optional UI sync
            try:
                w = self.window()
                if hasattr(w, "update_scene_asset_xform"):
                    w.update_scene_asset_xform(owner)
            except Exception:
                pass
        except Exception:
            pass

    def _rot_shared_q_from_euler_deg(self, rot_deg) -> QtGui.QQuaternion:
        rx, ry, rz = float(rot_deg[0]), float(rot_deg[1]), float(rot_deg[2])
        qx = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(1.0, 0.0, 0.0), rx)
        qy = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(0.0, 1.0, 0.0), ry)
        qz = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(0.0, 0.0, 1.0), rz)
        q = qz * qy * qx
        try:
            q = q.normalized()
        except Exception:
            pass
        return q


    def _rot_shared_euler_deg_from_q(self, q: QtGui.QQuaternion):
        # Extract Euler for R = Rz @ Ry @ Rx (matches your gizmo draw code)
        w = float(q.scalar())
        x = float(q.x())
        y = float(q.y())
        z = float(q.z())

        n = math.sqrt(w * w + x * x + y * y + z * z)
        if n > 1e-8:
            w /= n
            x /= n
            y /= n
            z /= n

        # quaternion -> rotation matrix (3x3)
        r00 = 1.0 - 2.0 * (y * y + z * z)
        r10 = 2.0 * (x * y + z * w)
        r20 = 2.0 * (x * z - y * w)
        r21 = 2.0 * (y * z + x * w)
        r22 = 1.0 - 2.0 * (x * x + y * y)

        # For RzRyRx: r20 = -sin(ry)
        sy = -r20
        sy = max(-1.0, min(1.0, sy))
        ry = math.asin(sy)
        cy = math.cos(ry)

        if abs(cy) > 1e-6:
            rx = math.atan2(r21, r22)
            rz = math.atan2(r10, r00)
        else:
            # gimbal: ry near +-90
            rx = 0.0
            r01 = 2.0 * (x * y - z * w)
            r11 = 1.0 - 2.0 * (x * x + z * z)
            rz = math.atan2(-r01, r11)

        return (math.degrees(rx), math.degrees(ry), math.degrees(rz))
            

    def _debug_status_lines(self, include_paths: bool = False) -> List[str]:
        w = int(self.width())
        h = int(self.height())

        lines = ["3D View Debug"]
        lines.append(f"Viewport: {w}x{h}")
        lines.append(f"FPS: {self._fps:5.1f}")
        lines.append(f"QOpenGLWidget: {'OK' if QOpenGLWidget is not None else 'missing'}")
        lines.append(f"GL context: {'OK' if hasattr(self, '_gl') else 'missing'}")
        ctx = None
        try:
            ctx = self.context()
        except Exception:
            ctx = None
        if ctx is not None:
            try:
                fmt = ctx.format()
                lines.append(f"Depth buffer: {fmt.depthBufferSize()}")
            except Exception:
                pass
        if self._use_moderngl:
            lines.append(f"Shaders: {'OK' if self._mgl_prog is not None else 'init'}")
        else:
            if QOpenGLShaderProgram is None:
                lines.append("Shaders: unavailable")
            elif self._shader_error:
                lines.append("Shaders: error")
            elif self._use_example_pipeline and self._example_program:
                lines.append("Shaders: OK")
            elif self._quad_program and self._mesh_program:
                lines.append("Shaders: OK")
            else:
                lines.append("Shaders: init")
        lines.append(f"QOpenGLTexture: {'OK' if QOpenGLTexture is not None else 'missing'}")
        lines.append(f"QOpenGLBuffer: {'OK' if QOpenGLBuffer is not None else 'missing'}")
        lines.append(f"VAO: {'OK' if self._vao is not None else 'none'}")
        lines.append(f"Mipmaps: {'on' if self._mipmaps_enabled else 'off'}")
        lines.append(f"Render paused: {'on' if self._render_paused else 'off'}")
        if self._use_moderngl:
            lines.append("Renderer: ModernGL")
            lines.append(f"ModernGL: {'OK' if self._mgl_ctx is not None else 'missing'}")
            lines.append(f"Camera FOV: {self._mgl_fov:.1f}")
            lines.append(f"Camera zoom: {self._mgl_camera_zoom:.2f}")
            if self._mgl_mesh_path:
                lines.append(f"Model: {Path(self._mgl_mesh_path).name}")
            if self._mgl_texture_override and self._mgl_texture_path:
                lines.append(f"Texture: {Path(self._mgl_texture_path).name}")
            elif self._mgl_texture_paths:
                names = [Path(p).name for p in self._mgl_texture_paths if p]
                if len(names) > 3:
                    shown = ", ".join(names[:3])
                    lines.append(f"Textures: {shown} (+{len(names) - 3} more)")
                else:
                    lines.append("Textures: " + ", ".join(names))
            else:
                lines.append("Texture: none")
            lines.append(f"Mesh indices: {self._mgl_mesh_vertex_count}")
            if self._mgl_uv_vertex_count:
                lines.append(f"UV verts: {self._mgl_uv_vertex_count}")
            lines.append(f"Grid lines: {self._mgl_grid_vertex_count}")
            if self._mgl_error:
                lines.append(f"ModernGL error: {self._mgl_error}")
        elif self._use_example_pipeline:
            lines.append("Example pipeline: on")
            shader_state = "error" if self._shader_error else ("OK" if self._example_program else "init")
            lines.append(f"Example shaders: {shader_state}")
            lines.append(f"Camera FOV: {self._example_fov:.1f}")
            distance = (self._example_cam_pos - self._example_cam_look).length()
            lines.append(f"Camera dist: {distance:.2f}")
            lines.append(f"Clip range: {self._example_near:.2f}-{self._example_far:.1f}")
            lines.append(f"Model extent: {self._example_model_extent:.2f}")
            lines.append(f"Scaled extent: {self._example_scaled_extent():.2f}")
            if self._example_model_path:
                lines.append(f"Example model: {Path(self._example_model_path).name}")
            if self._example_vao is not None:
                lines.append("Example VAO: OK")
            lines.append(f"Example verts: {self._example_draw_count}")
            if self._shader_error:
                lines.append(f"Example error: {self._shader_error}")
        if not self._render_scene_plane:
            lines.append("Scene tex: disabled")
        elif self._scene_texture is not None:
            lines.append("Scene tex: ready")
        else:
            lines.append("Scene tex: none")
        if not self._render_scene_models:
            lines.append("Meshes: disabled")
        else:
            lines.append(f"Meshes: {len(self._meshes)}")
        lines.append(f"Model scale: {self._model_scale_multiplier:.2f}x")
        if self._show_test_cube:
            lines.append("Test cube: on (1x1)")
        if self._debug_wire is not None and self._show_test_cube:
            lines.append("Cube wire: on")
        if self._use_moderngl:
            lines.append("Projection: perspective")
        elif self._use_example_pipeline:
            lines.append("Projection: perspective")
        else:
            lines.append(f"Projection: {'ortho' if self._use_ortho else 'perspective'}")
        mesh_paths = []
        for meta in self._mesh_meta.values():
            path_val = meta.get("path")
            if path_val:
                mesh_paths.append(str(path_val))
        if mesh_paths:
            names = [Path(p).name for p in mesh_paths if p]
            if names:
                shown = ", ".join(names[:3])
                if len(names) > 3:
                    shown += f" +{len(names) - 3}"
                lines.append(f"Mesh files: {shown}")
        if include_paths and mesh_paths:
            lines.append("Mesh paths: " + "; ".join(mesh_paths))
        lines.append(f"Scene content: {'blank' if self._scene_content_blank else 'ok'}")
        if self._use_moderngl:
            pass
        elif self._use_example_pipeline:
            if self._example_grid_count:
                lines.append(f"Example grid lines: {self._example_grid_count}")
        elif self._grid_count:
            lines.append(f"Grid lines: {self._grid_count}")
        rect = QtCore.QRectF(self._scene_src_rect)
        if not rect.isNull():
            lines.append(f"Scene rect: {int(rect.width())}x{int(rect.height())}")
        return lines

    def _draw_uv_overlay(self, painter: QtGui.QPainter) -> None:
        panel_size = 360.0
        margin = 12.0
        controls_h = float(getattr(self, "_controls_h", 0) or 0)
        right = self.width() - margin
        bottom = self.height() - margin - controls_h
        panel = QtCore.QRectF(right - panel_size, bottom - panel_size, panel_size, panel_size)
        if panel.top() < 10.0:
            panel.moveTop(10.0)
        if panel.left() < 10.0:
            panel.moveLeft(10.0)
        if (
            self._mgl_uv_cache is None
            or not self._mgl_uv_cache_rect.isValid()
            or self._mgl_uv_cache_rect != panel
        ):
            self._mgl_uv_cache_rect = QtCore.QRectF(panel)
            cache = QtGui.QPixmap(int(panel.width()), int(panel.height()))
            cache.fill(QtCore.Qt.transparent)
            uv_painter = QtGui.QPainter(cache)
            uv_painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            uv_painter.setPen(QtCore.Qt.NoPen)
            uv_painter.setBrush(QtGui.QColor(12, 14, 16, 235))
            uv_painter.drawRoundedRect(QtCore.QRectF(0, 0, panel.width(), panel.height()), 6, 6)
            if not self._mgl_uv_segments:
                uv_painter.setPen(QtGui.QColor("#e2e8f0"))
                uv_painter.drawText(QtCore.QRectF(0, 0, panel.width(), panel.height()), QtCore.Qt.AlignCenter, "No UVs")
            else:
                bounds = self._mgl_uv_bounds or (0.0, 1.0, 0.0, 1.0)
                u_min, u_max, v_min, v_max = bounds
                du = max(u_max - u_min, 1e-6)
                dv = max(v_max - v_min, 1e-6)
                pad = 8.0
                inner = QtCore.QRectF(
                    pad,
                    pad,
                    panel.width() - pad * 2,
                    panel.height() - pad * 2,
                )
                uv_painter.setPen(QtGui.QPen(QtGui.QColor("#d1d5db"), 1.0))
                for u0, v0, u1, v1 in self._mgl_uv_segments:
                    x0 = inner.left() + (u0 - u_min) / du * inner.width()
                    y0 = inner.top() + (1.0 - (v0 - v_min) / dv) * inner.height()
                    x1 = inner.left() + (u1 - u_min) / du * inner.width()
                    y1 = inner.top() + (1.0 - (v1 - v_min) / dv) * inner.height()
                    uv_painter.drawLine(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1))
            uv_painter.end()
            self._mgl_uv_cache = cache
        if self._mgl_uv_cache is not None:
            painter.drawPixmap(int(panel.left()), int(panel.top()), self._mgl_uv_cache)

    def _rotate_vec(self, x: float, y: float, z: float):
        cy = math.cos(self._cam_yaw)
        sy = math.sin(self._cam_yaw)
        cp = math.cos(self._cam_pitch)
        sp = math.sin(self._cam_pitch)
        x1 = x * cy + z * sy
        z1 = -x * sy + z * cy
        y1 = y
        y2 = y1 * cp - z1 * sp
        z2 = y1 * sp + z1 * cp
        x2 = x1
        return x2, y2, z2

    def _draw_axis_gizmo(self, p: QtGui.QPainter) -> None:
        vp = self.rect()
        if vp.isNull():
            return
        size = 46.0
        margin = 3.0
        radius = size * 0.405
        controls_h = 0.0
        controls = getattr(self, "_controls", None)
        if controls is not None and controls.isVisible():
            controls_h = float(getattr(self, "_controls_h", 0))
        origin = QtCore.QPointF(
            vp.left() + margin + radius,
            vp.bottom() - margin - radius - controls_h,
        )

        axes = (
            ("X", QtGui.QColor("#ff3b30"), (1.0, 0.0, 0.0)),
            ("Y", QtGui.QColor("#32d74b"), (0.0, 1.0, 0.0)),
            ("Z", QtGui.QColor("#0a84ff"), (0.0, 0.0, 1.0)),
        )
        projected = []
        max_len = 0.0
        for label, color, vec in axes:
            rot = getattr(self, "_gizmo_rot3", None)
            if rot is not None:
                # Use inverse(camera) = transpose(model-rot) because orbit is done by rotating the model
                x, y, z = vec
                x2 = rot[0][0]*x + rot[1][0]*y + rot[2][0]*z
                y2 = rot[0][1]*x + rot[1][1]*y + rot[2][1]*z
                z2 = rot[0][2]*x + rot[1][2]*y + rot[2][2]*z
            else:
                x2, y2, z2 = self._rotate_vec(x, y, z)
            length = math.hypot(x2, y2)
            if length > max_len:
                max_len = length
            projected.append((label, color, z2, x2, y2))
        if max_len <= 1e-6:
            return
        norm = radius / max_len
        for label, color, z2, vx, vy in projected:
            v2 = QtCore.QPointF(vx * norm, -vy * norm)
            pen = QtGui.QPen(color, 1.6)
            if z2 < 0.0:
                faded = QtGui.QColor(color)
                faded.setAlpha(140)
                pen.setColor(faded)
            p.setPen(pen)
            p.drawLine(origin, origin + v2)
            p.setPen(QtGui.QPen(color))
            p.drawText(origin + v2 + QtCore.QPointF(4.0, -2.0), label)

    def _reset_camera(self) -> None:
        bounds = self._mesh_bounds_world() if self._render_scene_models else None
        if bounds is not None:
            min_x, min_y, min_z, max_x, max_y, max_z = bounds
            self._cam_target = QtCore.QPointF((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)
            extent = max(max_x - min_x, max_y - min_y, max_z - min_z, self._cube_size)
        else:
            self._cam_target = QtCore.QPointF(0.0, 0.0)
            extent = self._cube_size
        if self._render_scene_plane:
            rect = QtCore.QRectF(self._scene_src_rect)
            if not rect.isNull():
                self._cam_target = rect.center()
                extent = max(rect.width(), rect.height(), self._cube_size)
        self._cam_yaw = 0.785398
        self._cam_pitch = -0.61548
        half_extent = extent * 0.5
        fov_rad = math.radians(self._fov_deg)
        fit_dist = half_extent / max(1e-3, math.tan(fov_rad * 0.5))
        self._cam_dist = max(self._min_cam_dist, fit_dist * 2.0)
        if self._show_test_cube and not self._render_scene_models and not self._render_scene_plane:
            self._cam_dist = max(self._cam_dist, 10.0)
        self._cam_focal = max(800.0, self.height() * 0.5 / max(1e-3, math.tan(fov_rad * 0.5)))
        self._debug_mesh_scale_base = self._cube_size
        self._debug_mesh_scale = self._debug_mesh_scale_base * self._model_scale_multiplier
        self._debug_mesh_center = QtCore.QPointF(self._cam_target)
        self._grid_center = QtCore.QPointF(self._cam_target)
        self._update_grid(self._world_extent)

    def mousePressEvent(self, e):
        if self._use_moderngl:
            # Safety: never stay grabbed between interactions
            try:
                if QtWidgets.QApplication.mouseGrabber() is self:
                    self.releaseMouse()
            except Exception:
                pass

            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False
            
            # --- Gizmo drag start (pick axis / rotate) ---
            if e.button() == QtCore.Qt.LeftButton:
                try:
                    owner = getattr(self, "_xform_gizmo_owner", None)
                    pos = getattr(self, "_xform_gizmo_pos", None)
                    if owner and pos and np is not None:
                        mode = getattr(self, "_xform_gizmo_mode", "translate") or "translate"
                        dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
                        px = float(e.x()) * dpr
                        py = float(e.y()) * dpr
                        vw = float(self.width()) * dpr
                        vh = float(self.height()) * dpr

                        renderer = getattr(self, "_mgl_renderer", None) or self
                        P = getattr(renderer, "_mgl_pick_proj", None)
                        V = getattr(renderer, "_mgl_pick_view", None)
                        M = getattr(renderer, "_mgl_pick_model", None)

                        if P is not None and V is not None and M is not None:
                            PV = (P @ V @ M).astype("f4")

                            def project(world_xyz):
                                p = np.array([world_xyz[0], world_xyz[1], world_xyz[2], 1.0], dtype="f4")
                                c = PV @ p
                                if abs(float(c[3])) < 1e-8:
                                    return None
                                ndc = c[:3] / c[3]
                                sx = (ndc[0] * 0.5 + 0.5) * vw
                                sy = (1.0 - (ndc[1] * 0.5 + 0.5)) * vh
                                return float(sx), float(sy)

                            def dist_pt_seg(px2, py2, ax, ay, bx, by):
                                abx = bx - ax
                                aby = by - ay
                                apx = px2 - ax
                                apy = py2 - ay
                                ab2 = abx * abx + aby * aby
                                if ab2 < 1e-8:
                                    dx = px2 - ax
                                    dy = py2 - ay
                                    return (dx * dx + dy * dy) ** 0.5
                                t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
                                cx = ax + t * abx
                                cy = ay + t * aby
                                dx = px2 - cx
                                dy = py2 - cy
                                return (dx * dx + dy * dy) ** 0.5

                            g = np.array([float(pos[0]), float(pos[1]), float(pos[2])], dtype="f4")
                            axis_len = 1.0
                            axes = {
                                "x": np.array([1.0, 0.0, 0.0], dtype="f4"),
                                "y": np.array([0.0, 1.0, 0.0], dtype="f4"),
                                "z": np.array([0.0, 0.0, 1.0], dtype="f4"),
                            }

                            p0 = project(g)
                            # --- shared rotate gizmo drag start ---
                            if getattr(self, "_xform_gizmo_mode", "") == "rotate":
                                rot_shared = getattr(self, "_rot_shared", None)
                                if not getattr(self, "_dbg_rot_shared_impl_once", False):
                                    self._dbg_rot_shared_impl_once = True
                                    try:
                                        import sys
                                        mod = sys.modules.get(rot_shared.__class__.__module__)
                                        mf = getattr(mod, "__file__", None)
                                    except Exception:
                                        mf = None

                                    print(
                                        "[ROT_SHARED_IMPL]"
                                        f" type={type(rot_shared)}"
                                        f" module={rot_shared.__class__.__module__}"
                                        f" file={mf}"
                                        f" xyz_px={getattr(rot_shared, 'xyz_ring_radius_px', lambda: None)()}"
                                        f" view_px={getattr(rot_shared, 'view_ring_radius_px', lambda: None)()}"
                                        f" view_scale={getattr(rot_shared, 'view_ring_scale', None)}"
                                        f" ui_scale={getattr(rot_shared, 'gizmo_ui_scale', None)}"
                                        f" screen_px={getattr(rot_shared, 'gizmo_screen_radius_px', None)}"
                                    )
                                center = getattr(self, "_rot_shared_center_px", None)
                                mvp = getattr(self, "_rot_shared_mvp", None)
                                owner = getattr(self, "_xform_gizmo_owner", None)
                                if rot_shared is not None and center is not None and mvp is not None and owner:
                                    mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
                                    hit = rot_shared.pick_axis_2d(
                                        widget=self,
                                        center=center,
                                        mouse_px=QtCore.QPointF(mp),
                                    )
                                    self._mgl_log(f"[ROT_SHARED] pick hit={hit} mode={getattr(self,'_xform_gizmo_mode',None)} owner={owner}")
                                    if hit is not None:
                                        self._rot_shared_dragging = True
                                        self._rot_shared_axis = hit  # "x" / "y" / "z" / "view"
                                        self._rot_shared_start_angle = rot_shared.angle_on_ring(center, QtCore.QPointF(mp))

                                        self._rot_shared_owner = owner

                                        start_rot, is_splat = self._get_owner_rot_deg(owner)
                                        self._rot_shared_start_rot = start_rot            # (rx, ry, rz) only
                                        self._rot_shared_is_splat = bool(is_splat)        # store flag separately
                                        # Axis rings: start shared constrained drag (smoketest behavior)
                                        if hit in ("x", "y", "z"):
                                            # Build start quaternion from current Euler degrees (Rz * Ry * Rx)
                                            try:
                                                rx = float(self._rot_shared_start_rot[0])
                                                ry = float(self._rot_shared_start_rot[1])
                                                rz = float(self._rot_shared_start_rot[2])
                                            except Exception:
                                                rx, ry, rz = 0.0, 0.0, 0.0

                                            qx = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(1.0, 0.0, 0.0), rx)
                                            qy = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(0.0, 1.0, 0.0), ry)
                                            qz = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(0.0, 0.0, 1.0), rz)
                                            q0 = (qz * qy * qx).normalized()

                                            axis_local = QtGui.QVector3D(1.0, 0.0, 0.0) if hit == "x" else (QtGui.QVector3D(0.0, 1.0, 0.0) if hit == "y" else QtGui.QVector3D(0.0, 0.0, 1.0))
                                            axis_world = q0.rotatedVector(axis_local)
                                            if axis_world.length() > 1e-6:
                                                axis_world = axis_world / axis_world.length()

                                            # store center in world for the drag (we already have g as np.array([...]) in this scope)
                                            self._rot_shared_axis_center_world = QtGui.QVector3D(float(g[0]), float(g[1]), float(g[2]))

                                            # camera position in the same space as g (inv(V@M) * origin)
                                            cam = None
                                            try:
                                                renderer = getattr(self, "_mgl_renderer", None)
                                                Vn = getattr(renderer, "_mgl_pick_view", None) if renderer is not None else None
                                                Mn = getattr(renderer, "_mgl_pick_model", None) if renderer is not None else None
                                                if Vn is not None and Mn is not None:
                                                    VM = (np.asarray(Vn, dtype=np.float32) @ np.asarray(Mn, dtype=np.float32)).astype(np.float32)
                                                    invVM = np.linalg.inv(VM)
                                                    cam4 = invVM @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                                                    cw = float(cam4[3]) if abs(float(cam4[3])) > 1e-8 else 1.0
                                                    c = cam4[:3] / cw
                                                    cam = QtGui.QVector3D(float(c[0]), float(c[1]), float(c[2]))
                                            except Exception:
                                                cam = None

                                            if cam is not None:
                                                # build mouse ray direction (same unproject style as your translate drag)
                                                try:
                                                    dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
                                                    px = float(mp.x()) * dpr
                                                    py = float(mp.y()) * dpr
                                                    vw = float(self.width()) * dpr
                                                    vh = float(self.height()) * dpr

                                                    renderer = getattr(self, "_mgl_renderer", None)
                                                    Pn = getattr(renderer, "_mgl_pick_proj", None) if renderer is not None else None
                                                    Vn = getattr(renderer, "_mgl_pick_view", None) if renderer is not None else None
                                                    Mn = getattr(renderer, "_mgl_pick_model", None) if renderer is not None else None
                                                    if Pn is not None and Vn is not None and Mn is not None:
                                                        PV = (np.asarray(Pn, dtype=np.float32) @ np.asarray(Vn, dtype=np.float32) @ np.asarray(Mn, dtype=np.float32)).astype(np.float32)
                                                        invPV = np.linalg.inv(PV)

                                                        x = (2.0 * (px / max(1.0, vw))) - 1.0
                                                        y = 1.0 - (2.0 * (py / max(1.0, vh)))
                                                        near = np.array([x, y, -1.0, 1.0], dtype=np.float32)
                                                        far  = np.array([x, y,  1.0, 1.0], dtype=np.float32)

                                                        pN = invPV @ near
                                                        pF = invPV @ far
                                                        pN = pN[:3] / pN[3]
                                                        pF = pF[:3] / pF[3]

                                                        rd = pF - pN
                                                        ln = float(np.linalg.norm(rd))
                                                        if ln > 1e-8:
                                                            rd = rd / ln
                                                            ray_d = QtGui.QVector3D(float(rd[0]), float(rd[1]), float(rd[2]))

                                                            center_w = self._rot_shared_axis_center_world
                                                            to_c = QtGui.QVector3D(center_w.x() - cam.x(), center_w.y() - cam.y(), center_w.z() - cam.z())
                                                            t = QtGui.QVector3D.dotProduct(to_c, ray_d)
                                                            p = QtGui.QVector3D(cam.x() + ray_d.x() * t, cam.y() + ray_d.y() * t, cam.z() + ray_d.z() * t)

                                                            v = QtGui.QVector3D(p.x() - center_w.x(), p.y() - center_w.y(), p.z() - center_w.z())
                                                            v = v - axis_world * QtGui.QVector3D.dotProduct(v, axis_world)
                                                            if v.length() > 1e-6:
                                                                start_dir = v / v.length()

                                                                rot_shared.begin_axis_drag(
                                                                    axis=str(hit),
                                                                    start_rot=q0,
                                                                    axis_world=axis_world,
                                                                    start_dir=start_dir,
                                                                )

                                                                # do NOT use the old _rot_shared_dragging path for xyz anymore
                                                                self._rot_shared_dragging = False
                                                                self._rot_shared_axis = None

                                                                e.accept()
                                                                return
                                                except Exception:
                                                    pass


                                        # If we clicked the camera-facing ring, start shared "view ring drag"
                                        if hit == "view":
                                            try:
                                                rot_shared.begin_view_ring_drag()
                                            except Exception:
                                                pass

                                            # Build start quaternion from current Euler degrees (Rz * Ry * Rx)
                                            try:
                                                rx = float(self._rot_shared_start_rot[0])
                                                ry = float(self._rot_shared_start_rot[1])
                                                rz = float(self._rot_shared_start_rot[2])
                                            except Exception:
                                                rx, ry, rz = 0.0, 0.0, 0.0

                                            qx = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(1.0, 0.0, 0.0), rx)
                                            qy = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(0.0, 1.0, 0.0), ry)
                                            qz = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(0.0, 0.0, 1.0), rz)
                                            self._rot_shared_view_q = (qz * qy * qx).normalized()

                                            # Camera forward in world space, pointing from camera to gizmo center
                                            forward_world = QtGui.QVector3D(0.0, 0.0, -1.0)
                                            try:
                                                renderer = getattr(self, "_mgl_renderer", None)
                                                Vn = getattr(renderer, "_mgl_pick_view", None) if renderer is not None else None
                                                Mn = getattr(renderer, "_mgl_pick_model", None) if renderer is not None else None
                                                if Vn is not None and Mn is not None:
                                                    VM = (np.asarray(Vn, dtype=np.float32) @ np.asarray(Mn, dtype=np.float32)).astype(np.float32)
                                                    invVM = np.linalg.inv(VM)
                                                    cam4 = invVM @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                                                    cw = float(cam4[3]) if abs(float(cam4[3])) > 1e-8 else 1.0
                                                    cam = cam4[:3] / cw

                                                    # NOTE: g already exists above as np.array([pos.x,pos.y,pos.z])
                                                    f = g - cam
                                                    ln = float(np.linalg.norm(f))
                                                    if ln > 1e-6:
                                                        f = f / ln
                                                        forward_world = QtGui.QVector3D(float(f[0]), float(f[1]), float(f[2]))
                                            except Exception:
                                                pass

                                            self._rot_shared_view_forward_world = forward_world

                                        e.accept()
                                        return

                            if False and mode == "rotate" and p0 is not None:
                                axis_radius = {}
                                max_axis_len = 0.0
                                ring_r = 0.9

                                def ring_radius(axis_name: str) -> float | None:
                                    if axis_name == "x":
                                        pts = [(0.0, ring_r, 0.0), (0.0, 0.0, ring_r)]
                                    elif axis_name == "y":
                                        pts = [(ring_r, 0.0, 0.0), (0.0, 0.0, ring_r)]
                                    else:
                                        pts = [(ring_r, 0.0, 0.0), (0.0, ring_r, 0.0)]
                                    dists = []
                                    for pt in pts:
                                        p1 = project((g[0] + pt[0], g[1] + pt[1], g[2] + pt[2]))
                                        if p1 is None:
                                            continue
                                        try:
                                            dx1 = float(p1[0]) - float(p0[0])
                                            dy1 = float(p1[1]) - float(p0[1])
                                            dists.append((dx1 * dx1 + dy1 * dy1) ** 0.5)
                                        except Exception:
                                            pass
                                    if not dists:
                                        return None
                                    return sum(dists) / float(len(dists))

                                for name in ("x", "y", "z"):
                                    r = ring_radius(name)
                                    if r is None:
                                        continue
                                    axis_radius[name] = r
                                    if r > max_axis_len:
                                        max_axis_len = r

                                # Avoid stealing orbit clicks far from the gizmo center.
                                try:
                                    dx0 = float(px) - float(p0[0])
                                    dy0 = float(py) - float(p0[1])
                                    max_center = max(32.0, max_axis_len + 16.0)
                                    if (dx0 * dx0 + dy0 * dy0) > (max_center * max_center):
                                        p0 = None
                                except Exception:
                                    pass

                                if p0 is not None and axis_radius:
                                    d_center = ((float(px) - float(p0[0])) ** 2 + (float(py) - float(p0[1])) ** 2) ** 0.5
                                    best_axis = None
                                    best_err = 1e30
                                    for name, r in axis_radius.items():
                                        err = abs(d_center - float(r))
                                        if err < best_err:
                                            best_err = err
                                            best_axis = name
                                    if best_axis is not None and best_err <= 18.0:
                                        # Determine kind directly from renderer state to avoid stale selection state.
                                        is_splat = False
                                        try:
                                            splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
                                            if not isinstance(splat_map, dict) or not splat_map:
                                                splat_map = getattr(renderer, "_mgl_scene_splats", None)
                                            if isinstance(splat_map, dict) and owner in splat_map:
                                                is_splat = True
                                        except Exception:
                                            is_splat = False

                                        # capture start rotation
                                        xf = {}
                                        get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                                        if callable(get_xf):
                                            xf = get_xf(owner) or {}
                                        rot = tuple((xf or {}).get("rot", (0.0, 0.0, 0.0)))

                                        self._xform_rotate_dragging = True
                                        self._xform_rotate_axis = best_axis
                                        self._xform_rotate_owner = owner
                                        self._xform_rotate_kind = "splat" if is_splat else "mesh"
                                        self._xform_rotate_center = (float(p0[0]), float(p0[1]))
                                        try:
                                            self._xform_rotate_start_angle = math.atan2(float(py) - float(p0[1]), float(px) - float(p0[0]))
                                        except Exception:
                                            self._xform_rotate_start_angle = None
                                        self._xform_rotate_start_rot = rot

                                        # Important: prevent old click-pick/orbit press state from interfering
                                        self._mgl_pick_press_pos = None
                                        self.setCursor(QtCore.Qt.CrossCursor)
                                        e.accept()
                                        return

                            if p0 is not None and mode != "rotate":
                                axis_proj = {}
                                max_axis_len = 0.0
                                for name, a in axes.items():
                                    p1 = project(g + a * axis_len)
                                    if p1 is None:
                                        continue
                                    axis_proj[name] = p1
                                    try:
                                        dx1 = float(p1[0]) - float(p0[0])
                                        dy1 = float(p1[1]) - float(p0[1])
                                        dist = (dx1 * dx1 + dy1 * dy1) ** 0.5
                                        if dist > max_axis_len:
                                            max_axis_len = dist
                                    except Exception:
                                        pass

                                # Avoid stealing orbit clicks far from the gizmo center.
                                try:
                                    dx0 = float(px) - float(p0[0])
                                    dy0 = float(py) - float(p0[1])
                                    max_center = max(24.0, max_axis_len + 12.0)
                                    if (dx0 * dx0 + dy0 * dy0) > (max_center * max_center):
                                        p0 = None
                                except Exception:
                                    pass

                            if p0 is not None and mode != "rotate":
                                best_axis = None
                                best_d = 1e30
                                for name, p1 in axis_proj.items():
                                    d = dist_pt_seg(px, py, p0[0], p0[1], p1[0], p1[1])
                                    if d < best_d:
                                        best_d = d
                                        best_axis = name

                                if best_axis is not None and best_d <= 14.0:
                                    # Determine kind directly from renderer state to avoid stale selection state.
                                    is_splat = False
                                    try:
                                        splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
                                        if not isinstance(splat_map, dict) or not splat_map:
                                            splat_map = getattr(renderer, "_mgl_scene_splats", None)
                                        if isinstance(splat_map, dict) and owner in splat_map:
                                            is_splat = True
                                    except Exception:
                                        is_splat = False

                                    self._xform_dragging = True
                                    self._xform_drag_axis = best_axis
                                    self._xform_drag_owner = owner
                                    self._xform_drag_kind = "splat" if is_splat else "mesh"
                                    self._xform_drag_start_pos = g.copy()
                                    self._xform_gizmo_pos_locked = True
                                    self._xform_drag_s0 = None

                                    # Log splat drag start with gizmo + xform state.
                                    try:
                                        if is_splat:
                                            xf = {}
                                            get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None)
                                            if callable(get_xf):
                                                xf = get_xf(owner) or {}
                                            xf_pos = tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))
                                            pivot = None
                                            bounds_map = (
                                                getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                                                or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
                                            )
                                            if isinstance(bounds_map, dict) and owner in bounds_map:
                                                mins, maxs = bounds_map.get(owner) or (None, None)
                                                if mins is not None and maxs is not None:
                                                    pivot = (
                                                        (float(mins[0]) + float(maxs[0])) * 0.5,
                                                        (float(mins[1]) + float(maxs[1])) * 0.5,
                                                        (float(mins[2]) + float(maxs[2])) * 0.5,
                                                    )
                                            self._mgl_log(
                                                "splat: drag_start owner="
                                                + str(owner)
                                                + " axis="
                                                + str(best_axis)
                                                + " gizmo_pos="
                                                + str(getattr(self, "_xform_gizmo_pos", None))
                                                + " xf_pos="
                                                + str(xf_pos)
                                                + " pivot="
                                                + str(pivot)
                                            )
                                    except Exception:
                                        pass

                                    # Important: prevent old click-pick/orbit press state from interfering
                                    self._mgl_pick_press_pos = None

                                    print("[GIZMO_PICK] axis=", best_axis, "d=", best_d, "owner=", owner, flush=True)
                                    self.setCursor(QtCore.Qt.SizeAllCursor)
                                    e.accept()
                                    return
                                
                except Exception as exc:
                    print("[GIZMO_PICK] failed:", exc, flush=True)
                
            if e.button() == QtCore.Qt.LeftButton and self._mgl_arcball is not None and alt_pressed:
                self._mgl_arcball.onClickLeftDown(e.x(), e.y())
                self._mgl_pick_press_pos = e.pos()
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                e.accept()
                return
            if e.button() == QtCore.Qt.LeftButton and not alt_pressed:
                # allow click-pick without orbiting
                self._mgl_pick_press_pos = e.pos()
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return
            if e.button() == QtCore.Qt.MiddleButton:
                if not alt_pressed:
                    e.ignore()
                    return
                self._mgl_prev_x = e.x()
                self._mgl_prev_y = e.y()
                self.setCursor(QtCore.Qt.OpenHandCursor)
                e.accept()
                return
            if e.button() == QtCore.Qt.RightButton:
                if not alt_pressed:
                    e.ignore()
                    return
                self._mgl_zoom_press_pos = e.pos()
                self._mgl_zoom_start = float(self._mgl_camera_zoom)
                self.setCursor(QtCore.Qt.SizeVerCursor)
                e.accept()
                return
        if self._use_example_pipeline:
            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False
            if e.button() == QtCore.Qt.LeftButton:
                if not alt_pressed:
                    e.ignore()
                    return
                self._orbit_dragging = True
                self._orbit_last_pos = e.pos()
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                e.accept()
                return
            if e.button() == QtCore.Qt.MiddleButton:
                if not alt_pressed:
                    e.ignore()
                    return
                self._pan_dragging = True
                self._pan_last_pos = e.pos()
                self.setCursor(QtCore.Qt.OpenHandCursor)
                e.accept()
                return
            if e.button() == QtCore.Qt.RightButton:
                if not alt_pressed:
                    e.ignore()
                    return
                self._dolly_dragging = True
                self._dolly_press_pos = e.pos()
                self.setCursor(QtCore.Qt.SizeVerCursor)
                e.accept()
                return
        if e.button() == QtCore.Qt.LeftButton:
            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False
            if not alt_pressed:
                e.ignore()
                return
            self._orbit_dragging = True
            self._orbit_last_pos = e.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == QtCore.Qt.MiddleButton:
            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False
            if not alt_pressed:
                e.ignore()
                return
            self._pan_dragging = True
            self._pan_last_pos = e.pos()
            self.setCursor(QtCore.Qt.OpenHandCursor)
            e.accept()
            return
        if e.button() == QtCore.Qt.RightButton:
            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False
            if not alt_pressed:
                e.ignore()
                return
            self._dolly_dragging = True
            self._dolly_press_pos = e.pos()
            self._dolly_start_dist = float(self._cam_dist)
            self.setCursor(QtCore.Qt.SizeVerCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._use_moderngl:
            # cache mouse pos for ROT_SHARED hover (logical pixels, matches project_to_screen usage)
            try:
                mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
                self._rot_shared_mouse_px = QtCore.QPointF(mp)
            except Exception:
                pass

            # Hover highlight needs repaints even when not dragging
            if (
                getattr(self, "_xform_gizmo_mode", "") == "rotate"
                and getattr(self, "_rot_shared", None) is not None
                and not getattr(self, "_rot_shared_dragging", False)
            ):
                self.update()

            # --- shared rotate gizmo drag move ---
            if getattr(self, "_rot_shared_dragging", False) and (e.buttons() & QtCore.Qt.LeftButton):
                rot_shared = getattr(self, "_rot_shared", None)
                center = getattr(self, "_rot_shared_center_px", None)
                owner = getattr(self, "_rot_shared_owner", None)
                axis = getattr(self, "_rot_shared_axis", None)
                start_angle = getattr(self, "_rot_shared_start_angle", None)
                start_rot = getattr(self, "_rot_shared_start_rot", None)
                # _get_owner_rot_deg can be (rot_deg, is_splat). If we accidentally stored that whole tuple, unwrap it.
                if isinstance(start_rot, (tuple, list)) and len(start_rot) == 2 and isinstance(start_rot[0], (tuple, list)):
                    start_rot = start_rot[0]

                if rot_shared is not None and center is not None and owner and axis and start_angle is not None and start_rot is not None:
                    mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
                    # VIEW ring drag must use shared incremental quaternion around camera forward (smoketest behavior)
                    if axis == "view":
                        qcur = getattr(self, "_rot_shared_view_q", None)
                        # camera forward in WORLD space from the current view matrix (this is the "on rails" axis)
                        forward_world = QtGui.QVector3D(0.0, 0.0, -1.0)
                        try:
                            renderer = getattr(self, "_mgl_renderer", None)
                            Vn = getattr(renderer, "_mgl_pick_view", None) if renderer is not None else None
                            if Vn is not None and np is not None:
                                invV = np.linalg.inv(np.asarray(Vn, dtype=np.float32))
                                f = invV[:3, :3] @ np.array([0.0, 0.0, -1.0], dtype=np.float32)
                                ln = float(np.linalg.norm(f))
                                if ln > 1e-6:
                                    f = f / ln
                                    forward_world = QtGui.QVector3D(float(f[0]), float(f[1]), float(f[2]))
                        except Exception:
                            pass


                        if qcur is not None:
                            qnew = rot_shared.update_view_ring_drag(QtCore.QPointF(mp), center, forward_world, qcur)
                            self._rot_shared_view_q = qnew

                            # Convert quaternion to Euler degrees in Rz * Ry * Rx order (matches your gizmo draw)
                            w = float(qnew.scalar()); x = float(qnew.x()); y = float(qnew.y()); z = float(qnew.z())
                            n = math.sqrt(w*w + x*x + y*y + z*z)
                            if n > 1e-8:
                                w /= n; x /= n; y /= n; z /= n

                            r00 = 1.0 - 2.0 * (y*y + z*z)
                            r10 = 2.0 * (x*y + z*w)
                            r20 = 2.0 * (x*z - y*w)
                            r21 = 2.0 * (y*z + x*w)
                            r22 = 1.0 - 2.0 * (x*x + y*y)

                            sy = -r20
                            sy = max(-1.0, min(1.0, sy))
                            ry = math.asin(sy)
                            cy = math.cos(ry)

                            if abs(cy) > 1e-6:
                                rx = math.atan2(r21, r22)
                                rz = math.atan2(r10, r00)
                            else:
                                rx = 0.0
                                r01 = 2.0 * (x*y - z*w)
                                r11 = 1.0 - 2.0 * (x*x + z*z)
                                rz = math.atan2(-r01, r11)

                            rx = math.degrees(rx); ry = math.degrees(ry); rz = math.degrees(rz)

                            is_splat = bool(getattr(self, "_rot_shared_is_splat", False))
                            self._set_owner_rot_deg(owner, (rx, ry, rz), is_splat)

                            self.update()
                            e.accept()
                            return
                        
                    rot_shared = getattr(self, "_rot_shared", None)
                    if rot_shared is not None and rot_shared.drag_axis.active and (e.buttons() & QtCore.Qt.LeftButton):
                        mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())

                        center_w = getattr(self, "_rot_shared_axis_center_world", None)
                        if center_w is None:
                            return

                        # camera world
                        cam = None
                        try:
                            renderer = getattr(self, "_mgl_renderer", None)
                            Vn = getattr(renderer, "_mgl_pick_view", None) if renderer is not None else None
                            Mn = getattr(renderer, "_mgl_pick_model", None) if renderer is not None else None
                            if Vn is not None and Mn is not None:
                                VM = (np.asarray(Vn, dtype=np.float32) @ np.asarray(Mn, dtype=np.float32)).astype(np.float32)
                                invVM = np.linalg.inv(VM)
                                cam4 = invVM @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                                cw = float(cam4[3]) if abs(float(cam4[3])) > 1e-8 else 1.0
                                c = cam4[:3] / cw
                                cam = QtGui.QVector3D(float(c[0]), float(c[1]), float(c[2]))
                        except Exception:
                            cam = None

                        if cam is None:
                            return

                        # mouse ray dir
                        try:
                            dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
                            px = float(mp.x()) * dpr
                            py = float(mp.y()) * dpr
                            vw = float(self.width()) * dpr
                            vh = float(self.height()) * dpr

                            renderer = getattr(self, "_mgl_renderer", None)
                            Pn = getattr(renderer, "_mgl_pick_proj", None) if renderer is not None else None
                            Vn = getattr(renderer, "_mgl_pick_view", None) if renderer is not None else None
                            Mn = getattr(renderer, "_mgl_pick_model", None) if renderer is not None else None
                            if Pn is None or Vn is None or Mn is None:
                                return

                            PV = (np.asarray(Pn, dtype=np.float32) @ np.asarray(Vn, dtype=np.float32) @ np.asarray(Mn, dtype=np.float32)).astype(np.float32)
                            invPV = np.linalg.inv(PV)

                            x = (2.0 * (px / max(1.0, vw))) - 1.0
                            y = 1.0 - (2.0 * (py / max(1.0, vh)))
                            near = np.array([x, y, -1.0, 1.0], dtype=np.float32)
                            far  = np.array([x, y,  1.0, 1.0], dtype=np.float32)

                            pN = invPV @ near
                            pF = invPV @ far
                            pN = pN[:3] / pN[3]
                            pF = pF[:3] / pF[3]

                            rd = pF - pN
                            ln = float(np.linalg.norm(rd))
                            if ln < 1e-8:
                                return
                            rd = rd / ln
                            ray_d = QtGui.QVector3D(float(rd[0]), float(rd[1]), float(rd[2]))

                            axis_world = rot_shared.drag_axis.axis_world
                            if axis_world is None or axis_world.length() < 1e-6:
                                return

                            to_c = QtGui.QVector3D(center_w.x() - cam.x(), center_w.y() - cam.y(), center_w.z() - cam.z())
                            t = QtGui.QVector3D.dotProduct(to_c, ray_d)
                            p = QtGui.QVector3D(cam.x() + ray_d.x() * t, cam.y() + ray_d.y() * t, cam.z() + ray_d.z() * t)

                            v = QtGui.QVector3D(p.x() - center_w.x(), p.y() - center_w.y(), p.z() - center_w.z())
                            v = v - axis_world * QtGui.QVector3D.dotProduct(v, axis_world)
                            if v.length() < 1e-6:
                                return
                            cur_dir = v / v.length()

                            qnew = rot_shared.update_axis_drag(cur_dir)
                            if qnew is None:
                                return

                            # reuse your existing quaternion->euler conversion (same as view ring branch)
                            w = float(qnew.scalar()); xq = float(qnew.x()); yq = float(qnew.y()); zq = float(qnew.z())
                            n = math.sqrt(w*w + xq*xq + yq*yq + zq*zq)
                            if n > 1e-8:
                                w /= n; xq /= n; yq /= n; zq /= n

                            r00 = 1.0 - 2.0 * (yq*yq + zq*zq)
                            r10 = 2.0 * (xq*yq + zq*w)
                            r20 = 2.0 * (xq*zq - yq*w)
                            r21 = 2.0 * (yq*zq + xq*w)
                            r22 = 1.0 - 2.0 * (xq*xq + yq*yq)

                            sy = -r20
                            sy = max(-1.0, min(1.0, sy))
                            ry = math.asin(sy)
                            cy = math.cos(ry)

                            if abs(cy) > 1e-6:
                                rx = math.atan2(r21, r22)
                                rz = math.atan2(r10, r00)
                            else:
                                rx = 0.0
                                r01 = 2.0 * (xq*yq - zq*w)
                                r11 = 1.0 - 2.0 * (xq*xq + zq*zq)
                                rz = math.atan2(-r01, r11)

                            rx = math.degrees(rx); ry = math.degrees(ry); rz = math.degrees(rz)

                            is_splat = bool(getattr(self, "_rot_shared_is_splat", False))
                            self._set_owner_rot_deg(owner, (rx, ry, rz), is_splat)

                            self.update()
                            e.accept()
                            return
                        except Exception:
                            pass

                    ang = math.atan2(float(mp.y() - center.y()), float(mp.x() - center.x()))
                    delta = float(ang) - float(start_angle)

                    while delta > math.pi:
                        delta -= 2.0 * math.pi
                    while delta < -math.pi:
                        delta += 2.0 * math.pi

                    delta_deg = math.degrees(delta)

                    ring_sign = {"x": -1.0, "y": -1.0, "z": -1.0}
                    delta_deg = float(delta_deg) * float(ring_sign.get(axis, 1.0))

                    rx, ry, rz = float(start_rot[0]), float(start_rot[1]), float(start_rot[2])
                    if axis == "x":
                        rx = rx + delta_deg
                    elif axis == "y":
                        ry = ry + delta_deg
                    elif axis == "z":
                        rz = rz + delta_deg
                    else:
                        # "view" ring: rotate around camera forward as a fallback, treat as Z for now
                        rz = rz + delta_deg

                    is_splat = bool(getattr(self, "_xform_gizmo_owner_kind", None) == "splat")
                    self._set_owner_rot_deg(owner, (rx, ry, rz), is_splat)

                    self.update()
                    e.accept()
                    return
                
              # --- Gizmo dragging (ModernGL) ---
            if False and getattr(self, "_xform_rotate_dragging", False) and (e.buttons() & QtCore.Qt.LeftButton):
                try:
                    if np is None:
                        return
                    axis = getattr(self, "_xform_rotate_axis", None)
                    owner = getattr(self, "_xform_rotate_owner", None)
                    start_rot = getattr(self, "_xform_rotate_start_rot", None)
                    center = getattr(self, "_xform_rotate_center", None)
                    start_angle = getattr(self, "_xform_rotate_start_angle", None)
                    if axis is None or owner is None or start_rot is None or center is None or start_angle is None:
                        return

                    dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
                    px = float(e.x()) * dpr
                    py = float(e.y()) * dpr
                    cx, cy = center

                    angle = math.atan2(py - cy, px - cx)
                    delta = angle - float(start_angle)
                    # unwrap for stability
                    while delta > math.pi:
                        delta -= 2.0 * math.pi
                    while delta < -math.pi:
                        delta += 2.0 * math.pi
                    delta_deg = math.degrees(delta)

                    rot = [float(start_rot[0]), float(start_rot[1]), float(start_rot[2])]
                    axis_key = axis
                    # Per-ring direction tuning (red ring inverted, blue inverted)
                    ring_sign = {"x": -1.0, "y": -1.0, "z": -1.0}
                    delta_deg = float(delta_deg) * float(ring_sign.get(axis_key, 1.0))
                    if axis_key == "x":
                        rot[0] += delta_deg
                    elif axis_key == "y":
                        rot[1] += delta_deg
                    else:
                        rot[2] += delta_deg

                    renderer = getattr(self, "_mgl_renderer", None) or self
                    is_splat = bool(getattr(self, "_xform_rotate_kind", None) == "splat")
                    self._mgl_set_scene_asset_xform(
                        owner,
                        rot=tuple(rot),
                        apply_to_scene_models=not is_splat,
                        use_splat_xform=bool(is_splat),
                    )
                    try:
                        w = self.window()
                        if hasattr(w, "update_scene_asset_xform"):
                            w.update_scene_asset_xform(owner)
                    except Exception:
                        pass
                    self.update()
                    e.accept()
                    return
                except Exception:
                    pass

            if getattr(self, "_xform_dragging", False) and (e.buttons() & QtCore.Qt.LeftButton):
                try:
                    if np is None:
                        return

                    axis = getattr(self, "_xform_drag_axis", None)
                    owner = getattr(self, "_xform_drag_owner", None)
                    g0 = getattr(self, "_xform_drag_start_pos", None)
                    if axis is None or owner is None or g0 is None:
                        return

                    # device pixels
                    dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
                    px = float(e.x()) * dpr
                    py = float(e.y()) * dpr
                    vw = float(self.width()) * dpr
                    vh = float(self.height()) * dpr

                    renderer = getattr(self, "_mgl_renderer", None) or self
                    P = getattr(renderer, "_mgl_pick_proj", None)
                    V = getattr(renderer, "_mgl_pick_view", None)
                    M = getattr(renderer, "_mgl_pick_model", None)
                    if P is None or V is None or M is None:
                        return

                    PV = (P @ V @ M).astype("f4")
                    invPV = np.linalg.inv(PV)

                    # mouse ray in world
                    x = (2.0 * (px / max(1.0, vw))) - 1.0
                    y = 1.0 - (2.0 * (py / max(1.0, vh)))
                    near = np.array([x, y, -1.0, 1.0], dtype="f4")
                    far  = np.array([x, y,  1.0, 1.0], dtype="f4")
                    pN = invPV @ near
                    pF = invPV @ far
                    pN = pN[:3] / pN[3]
                    pF = pF[:3] / pF[3]
                    ray_o = pN.astype("f4")
                    ray_d = (pF - pN).astype("f4")
                    n = float(np.linalg.norm(ray_d))
                    if n < 1e-8:
                        return
                    ray_d /= n

                    axes = {
                        "x": np.array([1.0, 0.0, 0.0], dtype="f4"),
                        "y": np.array([0.0, 1.0, 0.0], dtype="f4"),
                        "z": np.array([0.0, 0.0, 1.0], dtype="f4"),
                    }
                    a = axes.get(axis)
                    if a is None:
                        return

                    # Compute parameter "s" along axis line closest to the mouse ray
                    w0 = ray_o - g0
                    ad = float(np.dot(a, ray_d))
                    denom = 1.0 - ad * ad
                    if abs(denom) < 1e-6:
                        s = float(np.dot(a, w0))
                    else:
                        s = float((ad * float(np.dot(ray_d, w0)) - float(np.dot(a, w0))) / denom)

                    # On first move after pick, capture s0
                    s0 = getattr(self, "_xform_drag_s0", None)
                    if s0 is None:
                        self._xform_drag_s0 = s
                        s0 = s

                    delta = (float(s0) - s) * a
                    new_pos = g0 + delta

                    self._xform_gizmo_pos = (float(new_pos[0]), float(new_pos[1]), float(new_pos[2]))

                    # Move the selected asset (splat vs mesh) based on current renderer state.
                    is_splat = False
                    try:
                        splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
                        if not isinstance(splat_map, dict) or not splat_map:
                            splat_map = getattr(renderer, "_mgl_scene_splats", None)
                        if isinstance(splat_map, dict) and owner in splat_map:
                            is_splat = True
                    except Exception:
                        is_splat = False

                    # For splats, xform.pos is a translation offset from the original pivot.
                    set_pos = self._xform_gizmo_pos
                    if is_splat:
                        try:
                            pivot = None
                            bounds_map = getattr(renderer, "_mgl_scene_splats_bounds_local", None) or getattr(
                                renderer, "_mgl_scene_splat_bounds_by_owner", None
                            )
                            if isinstance(bounds_map, dict) and owner in bounds_map:
                                bmin, bmax = bounds_map.get(owner) or (None, None)
                                if bmin is not None and bmax is not None:
                                    pivot = (bmin + bmax) * 0.5
                            if pivot is not None:
                                set_pos = (
                                    float(new_pos[0] - pivot[0]),
                                    float(new_pos[1] - pivot[1]),
                                    float(new_pos[2] - pivot[2]),
                                )
                        except Exception:
                            set_pos = self._xform_gizmo_pos

                    self._mgl_set_scene_asset_xform(
                        owner,
                        pos=set_pos,
                        apply_to_scene_models=not is_splat,
                        use_splat_xform=bool(is_splat),
                    )
                    # Sync transform panel in the outliner (if visible)
                    try:
                        w = self.window()
                        if hasattr(w, "update_scene_asset_xform"):
                            w.update_scene_asset_xform(owner)
                    except Exception:
                        pass

                    # redraw
                    self.update()
                    e.accept()
                    return

                except Exception as exc:
                    print("[GIZMO_DRAG] failed:", exc, flush=True)

            if self._mgl_arcball is not None and (e.buttons() & QtCore.Qt.LeftButton):
                try:
                    if not (e.modifiers() & QtCore.Qt.AltModifier):
                        return
                except Exception:
                    pass
                self._mgl_arcball.onDrag(e.x(), e.y())
                self.update()  # ensure pick matrices stay fresh
                e.accept()
                return
            if e.buttons() & QtCore.Qt.MiddleButton and self._mgl_center is not None:
                try:
                    if not (e.modifiers() & QtCore.Qt.AltModifier):
                        return
                except Exception:
                    pass
                dx = e.x() - self._mgl_prev_x
                dy = e.y() - self._mgl_prev_y
                pan_scale = 0.01
                right = np.array([1.0, 0.0, 0.0], dtype="f4")
                up = np.array([0.0, 1.0, 0.0], dtype="f4")
                if self._mgl_arcball is not None:
                    rot = np.array(self._mgl_arcball.Transform[:3, :3], dtype="f4")
                    scale = np.linalg.norm(rot, axis=0)
                    denom = float(scale.mean()) if scale.size else 1.0
                    if denom > 1e-6:
                        rot = rot / denom
                    right = rot @ right
                    up = rot @ up
                delta = (-dx * pan_scale) * right + (dy * pan_scale) * up
                self._mgl_center += delta
                self._mgl_prev_x = e.x()
                self._mgl_prev_y = e.y()
                self.update()
                e.accept()
                return
            if e.buttons() & QtCore.Qt.RightButton and self._mgl_zoom_press_pos is not None:
                try:
                    if not (e.modifiers() & QtCore.Qt.AltModifier):
                        return
                except Exception:
                    pass
                dx = e.pos().x() - self._mgl_zoom_press_pos.x()
                dy = e.pos().y() - self._mgl_zoom_press_pos.y()
                distance = dx - dy
                exponent = abs(distance) / self._drag_divisor
                base = self._zoom_multiplier
                factor = base ** exponent
                start = self._mgl_zoom_start if self._mgl_zoom_start is not None else self._mgl_camera_zoom
                if distance > 0:
                    target = start / factor
                else:
                    target = start * factor
                self._mgl_camera_zoom = max(0.1, min(10000.0, target))
                self._mgl_zoom_start = self._mgl_camera_zoom
                self._mgl_zoom_press_pos = e.pos()
                self.update()
                e.accept()
                return
        if self._use_example_pipeline:
            if self._orbit_dragging and self._orbit_last_pos is not None:
                try:
                    if not (e.modifiers() & QtCore.Qt.AltModifier):
                        self._orbit_dragging = False
                        self._orbit_last_pos = None
                        self.setCursor(QtCore.Qt.ArrowCursor)
                        e.accept()
                        return
                except Exception:
                    pass
                if not (e.buttons() & QtCore.Qt.LeftButton):
                    self._orbit_dragging = False
                    self._orbit_last_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
                delta = e.pos() - self._orbit_last_pos
                self._orbit_last_pos = e.pos()
                self._example_orbit(QtCore.QPointF(delta))
                self.update()
                e.accept()
                return
            if self._pan_dragging and self._pan_last_pos is not None:
                try:
                    if not (e.modifiers() & QtCore.Qt.AltModifier):
                        self._pan_dragging = False
                        self._pan_last_pos = None
                        self.setCursor(QtCore.Qt.ArrowCursor)
                        e.accept()
                        return
                except Exception:
                    pass
                if not (e.buttons() & QtCore.Qt.MiddleButton):
                    self._pan_dragging = False
                    self._pan_last_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
                delta = e.pos() - self._pan_last_pos
                self._pan_last_pos = e.pos()
                self._example_pan(QtCore.QPointF(delta))
                self.update()
                e.accept()
                return
            if self._dolly_dragging and self._dolly_press_pos is not None:
                try:
                    if not (e.modifiers() & QtCore.Qt.AltModifier):
                        self._dolly_dragging = False
                        self._dolly_press_pos = None
                        self.setCursor(QtCore.Qt.ArrowCursor)
                        e.accept()
                        return
                except Exception:
                    pass
                if not (e.buttons() & QtCore.Qt.RightButton):
                    self._dolly_dragging = False
                    self._dolly_press_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
                delta = e.pos() - self._dolly_press_pos
                self._dolly_press_pos = e.pos()
                self._example_zoom(-float(delta.y()) / 60.0)
                self.update()
                e.accept()
                return
        if self._orbit_dragging and self._orbit_last_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._orbit_dragging = False
                    self._orbit_last_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.LeftButton):
                self._orbit_dragging = False
                self._orbit_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return
            delta = e.pos() - self._orbit_last_pos
            self._orbit_last_pos = e.pos()
            self._cam_yaw -= float(delta.x()) * self._orbit_sensitivity
            self._cam_pitch -= float(delta.y()) * self._orbit_sensitivity
            self._cam_pitch = max(-1.45, min(1.45, self._cam_pitch))
            self.update()
            e.accept()
            return
        if self._pan_dragging and self._pan_last_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._pan_dragging = False
                    self._pan_last_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.MiddleButton):
                self._pan_dragging = False
                self._pan_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return
            delta = e.pos() - self._pan_last_pos
            self._pan_last_pos = e.pos()
            fov_rad = math.radians(self._fov_deg)
            scale = (2.0 * self._cam_dist * math.tan(fov_rad * 0.5)) / max(1.0, self.height())
            self._cam_target = QtCore.QPointF(
                self._cam_target.x() - float(delta.x()) * scale,
                self._cam_target.y() - float(delta.y()) * scale,
            )
            self.update()
            e.accept()
            return
        if self._dolly_dragging and self._dolly_press_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._dolly_dragging = False
                    self._dolly_press_pos = None
                    self._dolly_start_dist = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.RightButton):
                self._dolly_dragging = False
                self._dolly_press_pos = None
                self._dolly_start_dist = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return
            dx = e.pos().x() - self._dolly_press_pos.x()
            dy = e.pos().y() - self._dolly_press_pos.y()
            distance = dy - dx
            exponent = abs(distance) / self._drag_divisor
            base = self._zoom_multiplier
            factor = base ** (-exponent) if distance > 0 else base ** (exponent)
            target = (self._dolly_start_dist or self._cam_dist) / factor
            self._cam_dist = max(self._min_cam_dist, min(self._max_cam_dist, target))
            self.update()
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        rot_shared = getattr(self, "_rot_shared", None)
        if e.button() == QtCore.Qt.LeftButton and rot_shared is not None and rot_shared.drag_axis.active:
            rot_shared.end_axis_drag()
            self._rot_shared_axis_center_world = None
            self.update()
            e.accept()
            return

        if self._use_moderngl:
            if getattr(self, "_rot_shared_dragging", False):
                self._rot_shared_dragging = False
                self._rot_shared_axis = None
                self._rot_shared_owner = None
                self._rot_shared_start_angle = None
                self._rot_shared_start_rot = None
                e.accept()
                return

            # --- 0) End rotation drag first ---
            if False and getattr(self, "_xform_rotate_dragging", False):
                self._xform_rotate_dragging = False
                self._xform_rotate_axis = None
                self._xform_rotate_owner = None
                self._xform_rotate_kind = None
                self._xform_rotate_start_angle = None
                self._xform_rotate_start_rot = None
                self._xform_rotate_center = None

                # Important: don't let a gizmo drag "fall through" into click-pick or orbit
                self._mgl_pick_press_pos = None

                try:
                    if QtWidgets.QApplication.mouseGrabber() is self:
                        self.releaseMouse()
                except Exception:
                    pass

                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return

            # --- 1) If we were dragging the gizmo, ALWAYS end that first ---
            if getattr(self, "_xform_dragging", False):
                # Log splat drag end with gizmo + xform state.
                try:
                    if getattr(self, "_xform_drag_kind", None) == "splat":
                        renderer = getattr(self, "_mgl_renderer", None) or self
                        owner = getattr(self, "_xform_drag_owner", None)
                        xf = {}
                        get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None)
                        if callable(get_xf) and owner:
                            xf = get_xf(owner) or {}
                        xf_pos = tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))
                        pivot = None
                        bounds_map = (
                            getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                            or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
                        )
                        if isinstance(bounds_map, dict) and owner in bounds_map:
                            mins, maxs = bounds_map.get(owner) or (None, None)
                            if mins is not None and maxs is not None:
                                pivot = (
                                    (float(mins[0]) + float(maxs[0])) * 0.5,
                                    (float(mins[1]) + float(maxs[1])) * 0.5,
                                    (float(mins[2]) + float(maxs[2])) * 0.5,
                                )
                        self._mgl_log(
                            "splat: drag_end owner="
                            + str(owner)
                            + " gizmo_pos="
                            + str(getattr(self, "_xform_gizmo_pos", None))
                            + " xf_pos="
                            + str(xf_pos)
                            + " pivot="
                            + str(pivot)
                            + " drag_start="
                            + str(getattr(self, "_xform_drag_start_pos", None))
                        )
                except Exception:
                    pass

                self._xform_dragging = False
                self._xform_drag_axis = None
                self._xform_drag_owner = None
                self._xform_drag_s0 = None
                self._xform_drag_kind = None
                self._xform_gizmo_pos_locked = False

                # Important: don't let a gizmo drag "fall through" into click-pick or orbit
                self._mgl_pick_press_pos = None

                # Safety: if we somehow grabbed the mouse, release it now
                try:
                    if QtWidgets.QApplication.mouseGrabber() is self:
                        self.releaseMouse()
                except Exception:
                    pass

                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return

            # --- 2) Normal click-pick (only if it was a click, not a drag) ---
            if e.button() == QtCore.Qt.LeftButton:
                try:
                    press = getattr(self, "_mgl_pick_press_pos", None)
                    if press is not None:
                        dx = abs(int(e.x()) - int(press.x()))
                        dy = abs(int(e.y()) - int(press.y()))
                        if dx <= 8 and dy <= 8:
                            renderer = getattr(self, "_mgl_renderer", None) or self
                            pick = getattr(renderer, "pick_owner_at", None)
                            if callable(pick):
                                dpr = 1.0
                                try:
                                    dpr = float(self.devicePixelRatioF())
                                except Exception:
                                    try:
                                        dpr = float(self.devicePixelRatio())
                                    except Exception:
                                        dpr = 1.0

                                px = int(e.x() * dpr)
                                py = int(e.y() * dpr)
                                vw = int(self.width() * dpr)
                                vh = int(self.height() * dpr)

                                owner = None
                                hit = None

                                pick_hit = getattr(renderer, "pick_hit_at", None)
                                if callable(pick_hit):
                                    owner, hit = pick_hit(px, py, vw, vh)
                                else:
                                    owner = pick(px, py, vw, vh)

                                if owner:
                                    w = self.window()
                                    if hasattr(w, "select_scene_asset"):
                                        w.select_scene_asset(owner)
                                    try:
                                        kind = getattr(renderer, "_mgl_last_pick_kind", None)
                                    except Exception:
                                        kind = None
                                    try:
                                        self._mgl_log(
                                            "scene: pick owner=" + str(owner) + " kind=" + str(kind)
                                        )
                                    except Exception:
                                        pass

                                    # Force gizmo to the owner pivot (stored xform) or bounds center fallback
                                    self._xform_gizmo_owner = owner
                                    try:
                                        self._xform_gizmo_owner_kind = getattr(renderer, "_mgl_last_pick_kind", None)
                                    except Exception:
                                        self._xform_gizmo_owner_kind = None

                                    try:
                                        # Use stored xform position when available
                                        xf = {}
                                        is_splat = False
                                        try:
                                            splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
                                            if not isinstance(splat_map, dict) or not splat_map:
                                                splat_map = getattr(renderer, "_mgl_scene_splats", None)
                                            if isinstance(splat_map, dict) and owner in splat_map:
                                                is_splat = True
                                        except Exception:
                                            is_splat = False

                                        get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                                        if callable(get_xf):
                                            xf = get_xf(owner) or {}
                                        xf_pos = tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))

                                        # For splats, xform.pos is an offset from the local pivot.
                                        pivot = None
                                        if is_splat:
                                            try:
                                                bounds_map = (
                                                    getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                                                    or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
                                                )
                                                if isinstance(bounds_map, dict) and owner in bounds_map:
                                                    mins, maxs = bounds_map.get(owner) or (None, None)
                                                    if mins is not None and maxs is not None:
                                                        pivot = (
                                                            (float(mins[0]) + float(maxs[0])) * 0.5,
                                                            (float(mins[1]) + float(maxs[1])) * 0.5,
                                                            (float(mins[2]) + float(maxs[2])) * 0.5,
                                                        )
                                            except Exception:
                                                pivot = None

                                        if is_splat:
                                            # splat xform.pos is an offset from local pivot
                                            if pivot is not None:
                                                self._xform_gizmo_pos = (
                                                    float(xf_pos[0] + pivot[0]),
                                                    float(xf_pos[1] + pivot[1]),
                                                    float(xf_pos[2] + pivot[2]),
                                                )
                                                self._xform_gizmo_pos_locked = True
                                            else:
                                                # fallback to bounds center
                                                bounds_map = (
                                                    getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                                                    or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
                                                )
                                                if isinstance(bounds_map, dict) and owner in bounds_map:
                                                    mins, maxs = bounds_map.get(owner) or (None, None)
                                                    if mins is not None and maxs is not None:
                                                        cx = (float(mins[0]) + float(maxs[0])) * 0.5
                                                        cy = (float(mins[1]) + float(maxs[1])) * 0.5
                                                        cz = (float(mins[2]) + float(maxs[2])) * 0.5
                                                        self._xform_gizmo_pos = (cx, cy, cz)
                                                        self._xform_gizmo_pos_locked = True
                                        else:
                                            # mesh: pos is already world pivot (even if zero)
                                            self._xform_gizmo_pos = (
                                                float(xf_pos[0]),
                                                float(xf_pos[1]),
                                                float(xf_pos[2]),
                                            )
                                            self._xform_gizmo_pos_locked = True

                                        # Log splat selection + gizmo placement for debugging.
                                        try:
                                            if is_splat:
                                                self._mgl_log(
                                                    "splat: select owner="
                                                    + str(owner)
                                                    + " xf_pos="
                                                    + str(xf_pos)
                                                    + " pivot="
                                                    + str(pivot)
                                                    + " gizmo_pos="
                                                    + str(getattr(self, "_xform_gizmo_pos", None))
                                                )
                                        except Exception:
                                            pass
                                    except Exception:
                                        pass

                                    # Allow outliner edits to reposition the gizmo after selection.
                                    try:
                                        self._xform_gizmo_pos_locked = False
                                    except Exception:
                                        pass

                                    self.update()
                                else:
                                    # Clicked empty space: keep gizmo/selection as-is
                                    try:
                                        self._mgl_log("scene: click empty -> keep gizmo")
                                    except Exception:
                                        pass
                                    self.update()
                except Exception:
                    pass

            # --- 3) Always end camera interactions cleanly ---
            if e.button() == QtCore.Qt.LeftButton:
                self._mgl_pick_press_pos = None
                if self._mgl_arcball is not None:
                    try:
                        self._mgl_arcball.onClickLeftUp()
                    except Exception:
                        pass

            if e.button() == QtCore.Qt.RightButton:
                self._mgl_zoom_press_pos = None
                self._mgl_zoom_start = None

            # Safety: if we somehow grabbed the mouse, release it now
            try:
                if QtWidgets.QApplication.mouseGrabber() is self:
                    self.releaseMouse()
            except Exception:
                pass

            self.setCursor(QtCore.Qt.ArrowCursor)
            super().mouseReleaseEvent(e)
            return

        # --- non-ModernGL paths unchanged ---
        if self._use_example_pipeline:
            if e.button() == QtCore.Qt.LeftButton:
                self._orbit_dragging = False
                self._orbit_last_pos = None
            if e.button() == QtCore.Qt.MiddleButton:
                self._pan_dragging = False
                self._pan_last_pos = None
            if e.button() == QtCore.Qt.RightButton:
                self._dolly_dragging = False
                self._dolly_press_pos = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            super().mouseReleaseEvent(e)
            return

        if e.button() == QtCore.Qt.LeftButton:
            self._orbit_dragging = False
            self._orbit_last_pos = None
        if e.button() == QtCore.Qt.MiddleButton:
            self._pan_dragging = False
            self._pan_last_pos = None
        if e.button() == QtCore.Qt.RightButton:
            self._dolly_dragging = False
            self._dolly_press_pos = None
            self._dolly_start_dist = None
        self.setCursor(QtCore.Qt.ArrowCursor)
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if self._use_moderngl:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    super().wheelEvent(e)
                    return
            except Exception:
                pass
            delta = e.angleDelta().y()
            if delta:
                self._mgl_camera_zoom += delta * 0.001
                if self._mgl_camera_zoom < 0.1:
                    self._mgl_camera_zoom = 0.1
                self.update()
            e.accept()
            return
        if self._use_example_pipeline:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    super().wheelEvent(e)
                    return
            except Exception:
                pass
            delta = e.angleDelta().y() / 120.0
            if delta:
                self._example_zoom(delta)
                self.update()
            e.accept()
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        try:
            key = e.key()
        except Exception:
            key = None
        if key == QtCore.Qt.Key_T:
            self._xform_gizmo_mode = "translate"
            try:
                self.update()
            except Exception:
                pass
            e.accept()
            return
        if key == QtCore.Qt.Key_R:
            self._xform_gizmo_mode = "rotate"
            try:
                self.update()
            except Exception:
                pass
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        # Keep gizmo when focus leaves the viewport (avoid hiding splats on UI click)
        try:
            try:
                self._mgl_log("scene: focus lost -> keep gizmo")
            except Exception:
                pass
            self.update()
        except Exception:
            pass
        super().focusOutEvent(e)
