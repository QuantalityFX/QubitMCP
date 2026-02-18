from __future__ import annotations

import math
import os
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
from .gl_loaders import load_model
from echograph.gizmo.rotate_gizmo_shared import quat_from_two_vectors
from echograph.ui.gl_view_paint import paint_gl as _paint_gl
from echograph.ui.gl_view_paint import paint_example as _paint_example
from echograph.ui.gl_view_example import upload_example_grid as _ex_upload_example_grid
from echograph.ui.gl_view_example import upload_example_vertices as _ex_upload_example_vertices
from echograph.ui.gl_view_example import init_example_pipeline as _ex_init_example_pipeline
from echograph.ui.gl_view_example import queue_example_model as _ex_queue_example_model
from echograph.ui.gl_view_example import apply_example_pending as _ex_apply_example_pending
from echograph.ui.gl_view_example import apply_example_bounds as _ex_apply_example_bounds
from echograph.ui.gl_view_example import example_scaled_extent as _ex_example_scaled_extent
from echograph.ui.gl_view_example import update_example_projection as _ex_update_example_projection
from echograph.ui.gl_view_example import update_example_transform as _ex_update_example_transform
from echograph.ui.gl_view_example import frame_example_camera as _ex_frame_example_camera
from echograph.ui.gl_view_example import update_example_camera_basis as _ex_update_example_camera_basis
from echograph.ui.gl_view_example import sync_example_gizmo as _ex_sync_example_gizmo
from echograph.ui.gl_view_example import example_view_matrix as _ex_example_view_matrix
from echograph.ui.gl_view_example import example_orbit as _ex_example_orbit
from echograph.ui.gl_view_example import example_pan as _ex_example_pan
from echograph.ui.gl_view_example import example_zoom as _ex_example_zoom
from echograph.ui import hotkeys_config
from echograph.ui.gl_view_example import build_example_program as _ex_build_example_program
from echograph.ui.gl_view_example import example_cube_data as _ex_cube_data
from echograph.ui.gl_view_example import example_grid_data as _ex_grid_data
from .gl_view_math import axis_proj_max_len as _gv_axis_proj_max_len
from .gl_view_math import axis_line_ray_param as _gv_axis_line_ray_param
from .gl_view_math import closest_unwrapped_euler as _gv_closest_unwrapped_euler
from .gl_view_math import dist_pt_seg as _gv_dist_pt_seg
from .gl_view_math import gizmo_screen_scale as _gv_gizmo_screen_scale
from .gl_view_math import plane_hit as _gv_plane_hit
from .gl_view_math import plane_normal_from_vm as _gv_plane_normal_from_vm
from .gl_view_math import project_local as _gv_project_local
from .gl_view_math import project_world as _gv_project_world
from .gl_view_math import ray_from_screen as _gv_ray_from_screen
from echograph.ui.fps_camera import FpsCamera
from echograph.rigging.turntable import TurntableController
from echograph.ui import actions

from typing import TYPE_CHECKING, Any, TypeAlias
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

ensure_assimp_dll()

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
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_DEPTH_TEST = 0x0B71

_HAS_MGL = moderngl is not None and np is not None and Matrix44 is not None

print("[GL_VIEW] LOADED FROM:", __file__)

class _ViewportHotkeyFilter(QtCore.QObject):
    def __init__(self, view):
        super().__init__(view)
        self._view = view

    def eventFilter(self, obj, ev):
        try:
            if ev.type() != QtCore.QEvent.KeyPress:
                return False
        except Exception:
            return False
        view = self._view
        if view is None:
            return False
        try:
            if not view.isVisible() or not view.isEnabled():
                return False
        except Exception:
            return False
        try:
            w = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
            in_view = False
            while w is not None:
                if w is view:
                    in_view = True
                    break
                w = w.parentWidget()
            if not in_view:
                return False
        except Exception:
            return False
        try:
            w = QtWidgets.QApplication.widgetAt(QtGui.QCursor.pos())
            if isinstance(
                w,
                (
                    QtWidgets.QLineEdit,
                    QtWidgets.QTextEdit,
                    QtWidgets.QPlainTextEdit,
                    QtWidgets.QSpinBox,
                    QtWidgets.QDoubleSpinBox,
                ),
            ):
                return False
        except Exception:
            pass
        try:
            if view._handle_viewport_hotkeys(ev, require_no_text_focus=False):
                return True
        except Exception:
            pass
        return False

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
        self._xform_mouse_px = None
        self._xform_hover_axis = None
        self._xform_hover_center = False
        self._xform_hover_center_px = None
        self._xform_hover_axis_proj = None
        self._xform_drag_plane_normal = None
        self._xform_drag_plane_start = None
        self._xform_use_local = True
        self._mgl_xform_space = "local"
        self._xform_hist_start = None
        self._gizmo_hotkeys_active = False

        self._xform_rotate_dragging = False
        self._xform_rotate_axis = None
        self._xform_rotate_owner = None
        self._xform_rotate_kind = None
        self._xform_rotate_start_angle = None
        self._xform_rotate_start_rot = None
        self._xform_rotate_center = None
        self._xform_drag_kind = None
        self._xform_drag_mode = None
        self._xform_drag_start_scl = None
        self._xform_scale_start_dist = None
        self._xform_scale_start_px = None
        self._xform_scale_axis_world = None
        self._xform_scale_center_px = None
        self._xform_drag_axis_world = None

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
            self._rot_owner_quat = {}
            # rotate gizmo backside clipping (match smoketest behavior)
            self._rot_clip_enabled = True
            self._rot_clip_frac = 0.30  # 0..1, higher = more aggressive backside trimming

            import sys
            mod = sys.modules.get(RotateGizmoShared.__module__)
            mf = getattr(mod, "__file__", None)
            print(
                "[ROT_SHARED_INIT]"
                f" file={mf}"
                f" ui_scale={getattr(self._rot_shared, 'gizmo_ui_scale', None)}"
                f" screen_px={getattr(self._rot_shared, 'gizmo_screen_radius_px', None)}"
                f" view_ring_scale={getattr(self._rot_shared, 'view_ring_scale', None)}"
            )

            print("[ROT_SHARED] ready")
        except Exception as exc:
            self._rot_shared = None
            self._rot_owner_quat = {}
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
                try:
                    fmt.setSamples(4)
                except Exception:
                    pass
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
        self._mgl_scene_splats: Dict[str, NDArray] = {}
        self._mgl_scene_splats_bounds_local: Dict[str, NDArray] = {}
        self._mgl_scene_splat_bounds_by_owner: Dict[str, NDArray] = {}
        self._mgl_scene_mesh_bounds_by_owner: Dict[str, NDArray] = {}
        self._mgl_scene_uvs_by_owner: Dict[str, NDArray] = {}
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
        self._mgl_proc_provider = None
        self._mgl_proc_label = ""
        self._mgl_proc_rev = -1
        self._mgl_proc_gpu_enabled = False
        self._mgl_proc_gpu_state = None
        self._mgl_proc_time = 0.0
        self._mgl_proc_glyph_tex = None
        self._mgl_proc_glyph_grid = (1, 1)
        self._mgl_proc_glyph_key = None
        self._mgl_scene_proc_textures_by_owner: Dict[str, Dict[str, object]] = {}
        self._mgl_frame_id = 0
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
        self._mgl_splat_log = False
        self._mgl_uv_overlay_enabled = False
        self._mgl_uv_segments: List[Tuple[float, float, float, float]] = []
        self._mgl_uv_bounds: Optional[Tuple[float, float, float, float]] = None
        self._mgl_uv_vertex_count = 0
        self._mgl_uv_cache = None
        self._mgl_uv_cache_rect = QtCore.QRectF()
        self._mgl_uv_bg_path = ""
        self._mgl_uv_cache_bg_key = ""
        self._mgl_grid_alpha = 0.90
        self._mgl_grid_size = 20.0
        self._mgl_grid_cells = 50
        self._mgl_grid_extend = 12.0
        self._mgl_grid_major_step = 10.0
        self._mgl_grid_major_boost = 1.15
        self._mgl_grid_fade_start = 0.55
        self._mgl_grid_fade_end = 0.95
        self._mgl_grid_fade_height = 10.0
        self._mgl_grid_fade_low_height = 0.0
        self._mgl_grid_fade_low_boost = 1.0
        self._mgl_grid_fade_zoom_exp = 1.1
        self._mgl_grid_fx_enabled = True
        self._mgl_gizmo_visible = True
        self._mgl_grid_visible = False
        self._mgl_fov = 60.0
        self._mgl_clip_far = 1000.0
        self._mgl_camera_zoom = 2.0
        self._mgl_min_zoom = 0.001
        self._mgl_pan_base = 0.01
        self._mgl_pan_zoom_exp_out = 1.2
        self._mgl_pan_zoom_boost = 10.0
        self._mgl_pan_zoom_threshold = 2.0
        self._mgl_pan_ref_zoom = None
        self._mgl_center = None
        self._mgl_base_center = None
        self._mgl_base_zoom = None
        self._mgl_scale = 1.0
        self._mgl_scale_multiplier = 1.0
        self._mgl_arcball = None
        self._mgl_orbit_locked = True
        self._mgl_orbit_dragging = False
        self._mgl_orbit_last_pos = None
        self._mgl_orbit_yaw = 0.0
        self._mgl_orbit_pitch = 0.0
        self._fly_mode_enabled = False
        self._orbit_cam_enabled = True
        self._mgl_grid_vertex_count = 0
        self._mgl_mesh_vertex_count = 0
        self._mgl_prev_x = 0
        self._mgl_prev_y = 0
        self._mgl_zoom_press_pos = None
        self._mgl_zoom_start = None
        self._mgl_zoom_center_start = None
        self._mgl_zoom_cam_start = None
        self._mgl_zoom_cam_dir = None
        self._mgl_zoom_ray_dir = None
        self._mgl_zoom_pan_scale = 0.02
        self._mgl_zoom_infinite = False
        self._turntable_ctrl = None

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
        self._cam_orbit_icon_locked = None
        self._cam_orbit_icon_free = None
        self._grid_icon_on = None
        self._grid_icon_off = None
        self._zoom_mode_icon_on = None
        self._zoom_mode_icon_off = None
        self._fly_mode_icon_on = None
        self._fly_mode_icon_off = None
        self._side_btn_size = 32
        self._side_btn_icon = 28
        self._side_btn_gap = 6
        self._side_btn_margin = 10
        self._side_btn_inner_pad = 2

        self._fps = 0.0
        self._fps_last_t = time.perf_counter()
        self._fps_ema = 0.0   # smoothed dt
        self._fps_timer = QtCore.QTimer(self)
        self._fps_timer.setInterval(16)  # ~60hz
        #self._fps_timer.setInterval(100)  # 10hz idle refresh
        self._fps_timer.timeout.connect(self._on_fps_tick)
        #self._fps_timer.start()

        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._fps_nav_active = False
        self._fps_nav_keys = set()
        self._fps_nav_last_t = time.perf_counter()
        self._fps_nav_speed = 2.0
        self._fps_nav_speed_min = 0.1
        self._fps_nav_speed_max = 50.0
        self._fps_nav_speed_step = 1.15
        self._fps_nav_look_last_pos = None
        self._fps_nav_cursor_anchor = None
        self._fps_nav_warping = False
        self._fps_nav_look_sens = 0.005
        self._fps_nav_boost = False
        self._fly_speed_mult = 1.0
        self._fps_camera = None
        self._fps_camera_active = False
        self._viewport_hotkey_filter = None
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                self._viewport_hotkey_filter = _ViewportHotkeyFilter(self)
                app.installEventFilter(self._viewport_hotkey_filter)
        except Exception:
            self._viewport_hotkey_filter = None
        self._build_scale_controls()
        self._build_debug_toggle_button()
        self._build_debug_copy_button()
        self._build_camera_orbit_button()
        self._build_fly_mode_button()
        self._build_grid_button()
        self._build_zoom_mode_button()
        


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
        try:
            settings = getattr(scene, "_view_settings", None)
            if isinstance(settings, dict):
                speed_mult = settings.get("fly_speed_mult", None)
                if speed_mult is not None:
                    self._apply_fly_speed_multiplier(float(speed_mult), sync_ui=True, sync_scene=False)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_controls", None) is not None:
            h = int(getattr(self, "_controls_h", 44))
            self._controls.setGeometry(0, max(0, self.height() - h), self.width(), h)
        toggle_frame = getattr(self, "_debug_toggle_btn_frame", None)
        toggle = getattr(self, "_debug_toggle_btn", None)
        if toggle_frame is not None:
            toggle_frame.setGeometry(
                self._side_btn_margin,
                self._side_btn_margin,
                self._side_btn_size,
                self._side_btn_size,
            )
        elif toggle is not None:
            toggle.setGeometry(
                self._side_btn_margin,
                self._side_btn_margin,
                self._side_btn_size,
                self._side_btn_size,
            )
        y = self._side_btn_margin + self._side_btn_size + self._side_btn_gap
        frame_frame = getattr(self, "_frame_btn_frame", None)
        frame_btn = getattr(self, "_frame_btn", None)
        if frame_frame is not None:
            frame_frame.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        elif frame_btn is not None:
            frame_btn.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        snap_frame = getattr(self, "_snapgrab_btn_frame", None)
        snap_btn = getattr(self, "_snapgrab_btn", None)
        if snap_frame is not None:
            snap_frame.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        elif snap_btn is not None:
            snap_btn.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        grid_frame = getattr(self, "_grid_btn_frame", None)
        grid_btn = getattr(self, "_grid_btn", None)
        if grid_frame is not None:
            grid_frame.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        elif grid_btn is not None:
            grid_btn.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        zoom_frame = getattr(self, "_zoom_mode_btn_frame", None)
        zoom_btn = getattr(self, "_zoom_mode_btn", None)
        if zoom_frame is not None:
            zoom_frame.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        elif zoom_btn is not None:
            zoom_btn.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        xform_frame = getattr(self, "_xform_space_btn_frame", None)
        xform_btn = getattr(self, "_xform_space_btn", None)
        if xform_frame is not None:
            xform_frame.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        elif xform_btn is not None:
            xform_btn.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        orbit_frame = getattr(self, "_cam_orbit_btn_frame", None)
        orbit_btn = getattr(self, "_cam_orbit_btn", None)
        if orbit_frame is not None:
            orbit_frame.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        elif orbit_btn is not None:
            orbit_btn.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        fly_frame = getattr(self, "_fly_mode_btn_frame", None)
        fly_btn = getattr(self, "_fly_mode_btn", None)
        if fly_frame is not None:
            fly_frame.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
        elif fly_btn is not None:
            fly_btn.setGeometry(self._side_btn_margin, y, self._side_btn_size, self._side_btn_size)
            y += self._side_btn_size + self._side_btn_gap
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

    def _on_fps_tick(self) -> None:
        try:
            self._tick_fps_nav()
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass

    def _fps_apply_look(self, dx: float, dy: float) -> None:
        cam = getattr(self, "_fps_camera", None)
        if cam is None:
            return
        try:
            sens = float(getattr(self, "_fps_nav_look_sens", 0.005))
        except Exception:
            sens = 0.005
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return
        roll_locked = bool(getattr(self, "_mgl_orbit_locked", True))
        try:
            cam.apply_look(dx, dy, sens=sens, roll_locked=roll_locked)
        except Exception:
            return

    def _fps_cam_sync_from_orbit(self) -> None:
        if np is None or Matrix44 is None:
            return
        arc = getattr(self, "_mgl_arcball", None)
        if arc is None or not hasattr(arc, "Transform"):
            return
        try:
            zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
        except Exception:
            zoom = 0.0
        cam = getattr(self, "_fps_camera", None)
        if cam is None:
            try:
                cam = FpsCamera()
                self._fps_camera = cam
            except Exception:
                return
        roll_locked = bool(getattr(self, "_mgl_orbit_locked", True))
        try:
            lookat = Matrix44.look_at(
                (0.0, 0.0, float(zoom)),
                (0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
            )
            center = getattr(self, "_mgl_center", None)
            if center is not None and np is not None:
                try:
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
            view = (lookat * transform).astype("f4")
            cam.set_from_view_matrix(np.array(view, dtype=np.float32), roll_locked=roll_locked)
        except Exception:
            return

    def _fps_cam_sync_orbit_from_camera(self) -> None:
        if np is None or Matrix44 is None:
            return
        cam = getattr(self, "_fps_camera", None)
        if cam is None:
            return
        arc = getattr(self, "_mgl_arcball", None)
        if arc is None or not hasattr(arc, "Transform"):
            return
        try:
            zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
        except Exception:
            zoom = 0.0
        try:
            roll_locked = bool(getattr(self, "_mgl_orbit_locked", True))
            lookat = Matrix44.look_at(
                (0.0, 0.0, float(zoom)),
                (0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
            )
            view = cam.view_matrix(roll_locked=roll_locked)
            lmat = np.array(lookat, dtype=np.float32)
            vmat = np.array(view, dtype=np.float32)
            tmat = np.linalg.inv(lmat) @ vmat
            rot = np.array(tmat[:3, :3], dtype=np.float32)
            trow = np.array(tmat[3, :3], dtype=np.float32)
            try:
                center = -(np.linalg.inv(rot.T) @ trow)
                self._mgl_center = center.astype("f4")
            except Exception:
                center = None
            try:
                arc.Transform = tmat.astype("f4")
            except Exception:
                try:
                    arc.Transform[:3, :3] = rot
                    arc.Transform[3, :3] = trow
                except Exception:
                    pass
        except Exception:
            return
        try:
            self._mgl_arcball_sync_after_set_transform()
        except Exception:
            pass
        try:
            if bool(getattr(self, "_mgl_orbit_locked", True)):
                self._sync_locked_orbit_from_arcball()
        except Exception:
            pass

    def _fps_log_camera_state(self, tag: str, throttle_key: str | None = None, interval: float = 0.25) -> None:
        try:
            if not bool(getattr(self, "_mgl_splat_log", True)):
                return
        except Exception:
            return
        try:
            cam_world = getattr(self, "_mgl_cam_world", None)
            center = getattr(self, "_mgl_center", None)
            zoom = getattr(self, "_mgl_camera_zoom", None)
            arc = getattr(self, "_mgl_arcball", None)
            cam_calc = None
            if np is not None and arc is not None and hasattr(arc, "Transform"):
                try:
                    rot = np.array(arc.Transform[:3, :3], dtype=np.float32)
                    scale = np.linalg.norm(rot, axis=0)
                    denom = float(scale.mean()) if scale.size else 1.0
                    if denom > 1e-6:
                        rot = rot / denom
                    if center is None:
                        center_vec = np.zeros(3, dtype="f4")
                    else:
                        center_vec = np.array(center, dtype="f4")
                    z = float(zoom) if zoom is not None else 0.0
                    cam_local = np.array([0.0, 0.0, z], dtype=np.float32)
                    cam_calc = center_vec + (rot.T @ cam_local)
                except Exception:
                    cam_calc = None
            msg = (
                f"[FPS_CAM] {tag}"
                f" cam_world={cam_world}"
                f" cam_calc={tuple(cam_calc.tolist()) if cam_calc is not None else None}"
                f" center={center}"
                f" zoom={zoom}"
                f" nav_active={bool(getattr(self, '_fps_nav_active', False))}"
            )
            if throttle_key:
                self._mgl_log_throttled(throttle_key, msg, interval=interval)
            else:
                self._mgl_log(msg)
        except Exception:
            pass

    def _tick_fps_nav(self) -> None:
        now = time.perf_counter()
        last = float(getattr(self, "_fps_nav_last_t", now))
        dt = max(0.0, min(0.1, float(now - last)))
        self._fps_nav_last_t = now

        if not bool(getattr(self, "_fps_nav_active", False)) and not bool(getattr(self, "_fly_mode_enabled", False)):
            return
        if not bool(getattr(self, "_use_moderngl", False)):
            return
        keys = getattr(self, "_fps_nav_keys", None)
        if not keys:
            return
        if np is None:
            return

        cam = getattr(self, "_fps_camera", None)
        if cam is not None and bool(getattr(self, "_fps_camera_active", False)):
            forward_amt = 0.0
            right_amt = 0.0
            if "w" in keys:
                forward_amt += 1.0
            if "s" in keys:
                forward_amt -= 1.0
            if "a" in keys:
                right_amt -= 1.0
            if "d" in keys:
                right_amt += 1.0
            ln = math.sqrt((forward_amt * forward_amt) + (right_amt * right_amt))
            if ln < 1e-6:
                return
            forward_amt /= ln
            right_amt /= ln
            speed = float(getattr(self, "_fps_nav_speed", 2.0))
            speed *= float(getattr(self, "_fly_speed_mult", 1.0))
            if bool(getattr(self, "_fps_nav_boost", False)):
                speed *= 2.0
            try:
                zoom = float(getattr(self, "_mgl_camera_zoom", 1.0))
                speed *= max(0.2, min(4.0, zoom * 0.25))
            except Exception:
                pass
            delta = float(speed) * float(dt)
            roll_locked = bool(getattr(self, "_mgl_orbit_locked", True))
            try:
                cam.move(forward_amt * delta, right_amt * delta, 0.0, roll_locked=roll_locked)
            except Exception:
                pass
            return

        right = np.array([1.0, 0.0, 0.0], dtype="f4")
        up = np.array([0.0, 1.0, 0.0], dtype="f4")
        forward = np.array([0.0, 0.0, -1.0], dtype="f4")
        if self._mgl_arcball is not None:
            rot = np.array(self._mgl_arcball.Transform[:3, :3], dtype="f4")
            scale = np.linalg.norm(rot, axis=0)
            denom = float(scale.mean()) if scale.size else 1.0
            if denom > 1e-6:
                rot = rot / denom
            right = rot @ right
            up = rot @ up
            forward = rot @ forward

        move = np.zeros(3, dtype="f4")
        if "w" in keys:
            move += forward
        if "s" in keys:
            move -= forward
        if "a" in keys:
            move -= right
        if "d" in keys:
            move += right
        ln = float(np.linalg.norm(move))
        if ln < 1e-6:
            return
        move /= ln

        speed = float(getattr(self, "_fps_nav_speed", 2.0))
        speed *= float(getattr(self, "_fly_speed_mult", 1.0))
        if bool(getattr(self, "_fps_nav_boost", False)):
            speed *= 2.0
        try:
            zoom = float(getattr(self, "_mgl_camera_zoom", 1.0))
            speed *= max(0.2, min(4.0, zoom * 0.25))
        except Exception:
            pass

        delta = move * float(speed) * float(dt)
        center = getattr(self, "_mgl_center", None)
        if center is None:
            center = np.zeros(3, dtype="f4")
        else:
            center = np.array(center, dtype="f4")
        self._mgl_center = center + delta

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

            self._frame_btn = QtWidgets.QToolButton(self)
            self._frame_btn.setToolTip("Frame")
            self._frame_btn.setIconSize(QtCore.QSize(self._side_btn_icon, self._side_btn_icon))
            self._frame_btn.setFixedSize(self._side_btn_size, self._side_btn_size)
            frame_icon = Path(__file__).resolve().parents[2] / "icons" / "Frame_Icon.png"
            if frame_icon.exists():
                self._frame_btn.setIcon(QtGui.QIcon(str(frame_icon)))
            else:
                self._frame_btn.setText("Frame")
            self._frame_btn.clicked.connect(self._on_frame_clicked)
            self._apply_side_icon_style(self._frame_btn, active=False)
            self._frame_btn_frame = self._wrap_side_button(self._frame_btn, "_frame_btn_frame")
            self._frame_btn.show()

            self._camlog_btn = QtWidgets.QPushButton("LogCam")
            self._camlog_btn.setToolTip("Write current camera/orbit state to log")
            self._camlog_btn.clicked.connect(self._on_camlog_clicked)
            layout.addWidget(self._camlog_btn, 0)

            self._snapgrab_btn = QtWidgets.QToolButton(self)
            self._snapgrab_btn.setToolTip("Snapshot")
            self._snapgrab_btn.setIconSize(QtCore.QSize(self._side_btn_icon, self._side_btn_icon))
            self._snapgrab_btn.setFixedSize(self._side_btn_size, self._side_btn_size)
            icon_path = Path(__file__).resolve().parents[2] / "icons" / "screengrab _Icon_s_001.png"
            if icon_path.exists():
                self._snapgrab_btn.setIcon(QtGui.QIcon(str(icon_path)))
            self._snapgrab_btn.clicked.connect(self._on_snapgrab_clicked)
            self._apply_side_icon_style(self._snapgrab_btn, active=False)
            self._snapgrab_btn_frame = self._wrap_side_button(self._snapgrab_btn, "_snapgrab_btn_frame")
            self._snapgrab_btn.show()

            self._xform_space_btn = QtWidgets.QToolButton(self)
            self._xform_space_btn.setToolTip("Gizmo Space: World")
            self._xform_space_btn.setCheckable(True)
            self._xform_space_btn.setIconSize(QtCore.QSize(self._side_btn_icon, self._side_btn_icon))
            self._xform_space_btn.setFixedSize(self._side_btn_size, self._side_btn_size)
            self._xform_space_btn.clicked.connect(self._on_xform_space_toggled)
            self._update_xform_space_button()
            self._xform_space_btn_frame = self._wrap_side_button(self._xform_space_btn, "_xform_space_btn_frame")
            self._xform_space_btn.show()
            
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

                # Grid toggle lives in the left toolbar.
                self._mgl_uv_toggle = QtWidgets.QCheckBox("UVs")
                self._mgl_uv_toggle.setChecked(bool(self._mgl_uv_overlay_enabled))
                self._mgl_uv_toggle.toggled.connect(self._on_mgl_uv_toggled)
                layout.addWidget(self._mgl_uv_toggle, 0)

                self._mgl_grid_fx_toggle = QtWidgets.QCheckBox("Grid FX")
                self._mgl_grid_fx_toggle.setChecked(bool(self._mgl_grid_fx_enabled))
                self._mgl_grid_fx_toggle.toggled.connect(self._on_mgl_grid_fx_toggled)
                layout.addWidget(self._mgl_grid_fx_toggle, 0)

                self._mgl_gizmo_toggle = QtWidgets.QCheckBox("Gizmo")
                self._mgl_gizmo_toggle.setChecked(bool(self._mgl_gizmo_visible))
                self._mgl_gizmo_toggle.toggled.connect(self._on_mgl_gizmo_toggled)
                layout.addWidget(self._mgl_gizmo_toggle, 0)

                if self._turntable_ctrl is None:
                    self._turntable_ctrl = TurntableController(self)
                try:
                    turntable_panel = self._turntable_ctrl.build_panel(controls)
                    layout.addWidget(turntable_panel, 0)
                except Exception:
                    pass

            layout.addStretch(1)
            self._controls = controls
            self._controls_h = 44
            self._controls.show()
        except Exception:
            self._controls = None
            self._controls_h = 0

    def _on_fly_speed_mult_changed(self, value: int | None = None) -> None:
        if value is None:
            slider = getattr(self, "_fly_speed_slider", None)
            if slider is not None:
                try:
                    value = int(slider.value())
                except Exception:
                    value = None
        if value is None:
            return
        try:
            mult = float(value) / 100.0
        except Exception:
            mult = 1.0
        self._apply_fly_speed_multiplier(mult, sync_ui=True, sync_scene=True)

    def _apply_fly_speed_multiplier(
        self,
        mult: float,
        *,
        sync_ui: bool = True,
        sync_scene: bool = True,
    ) -> None:
        try:
            mult = min(10.0, max(0.1, float(mult)))
        except Exception:
            mult = 1.0
        self._fly_speed_mult = mult
        if sync_ui:
            label = getattr(self, "_fly_speed_label", None)
            if label is not None:
                label.setText(f"Fly {mult:.2f}x")
            slider = getattr(self, "_fly_speed_slider", None)
            if slider is not None:
                try:
                    slider.blockSignals(True)
                    slider.setValue(int(round(mult * 100.0)))
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
                    settings["fly_speed_mult"] = float(mult)
                    scene._view_settings = settings
                except Exception:
                    pass
        self.update()

    def _load_xform_space_icons(self) -> None:
        if getattr(self, "_xform_space_icon_world", None) is not None:
            return
        icon_world = None
        icon_local = None
        try:
            root = Path(__file__).resolve().parents[2]
            world_path = root / "icons" / "WorldGizmoOn_Icon.png"
            local_path = root / "icons" / "WorldGizmoOff_Icon.png"
            if world_path.exists():
                icon_world = QtGui.QIcon(str(world_path))
            if local_path.exists():
                icon_local = QtGui.QIcon(str(local_path))
        except Exception:
            icon_world = None
            icon_local = None
        self._xform_space_icon_world = icon_world
        self._xform_space_icon_local = icon_local

    def _update_xform_space_button(self) -> None:
        btn = getattr(self, "_xform_space_btn", None)
        if btn is None:
            return
        use_local = bool(getattr(self, "_xform_use_local", False))
        btn.setChecked(use_local)
        self._load_xform_space_icons()
        icon_world = getattr(self, "_xform_space_icon_world", None)
        icon_local = getattr(self, "_xform_space_icon_local", None)
        if use_local:
            btn.setToolTip("Gizmo Space: Local")
            if icon_local is not None:
                btn.setIcon(icon_local)
                btn.setText("")
            else:
                btn.setText("Local")
        else:
            btn.setToolTip("Gizmo Space: World")
            if icon_world is not None:
                btn.setIcon(icon_world)
                btn.setText("")
            else:
                btn.setText("World")
        inner = max(1, int(self._side_btn_size - (2 * self._side_btn_inner_pad)))
        icon = min(self._side_btn_icon, inner)
        btn.setIconSize(QtCore.QSize(icon, icon))
        try:
            btn.setFixedSize(inner, inner)
        except Exception:
            pass
        self._apply_side_icon_style(btn, active=use_local)

    def _on_xform_space_toggled(self, checked=None) -> None:
        if checked is None:
            checked = bool(getattr(self, "_xform_space_btn", None) and self._xform_space_btn.isChecked())
        self._xform_use_local = bool(checked)
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            setattr(renderer, "_mgl_xform_space", "local" if self._xform_use_local else "world")
        except Exception:
            pass
        self._update_xform_space_button()
        try:
            self.update()
        except Exception:
            pass

    def _on_mgl_gizmo_toggled(self, checked: bool) -> None:
        try:
            self._mgl_gizmo_visible = bool(checked)
        except Exception:
            self._mgl_gizmo_visible = True
        try:
            self.update()
        except Exception:
            pass

    def _begin_xform_history(self, owner: str) -> None:
        if not owner:
            return
        if self._xform_hist_start is not None:
            return
        try:
            win = self.window()
        except Exception:
            win = None
        if win is None:
            return
        scene_node = getattr(win, "_active_scene_node", None)
        if scene_node is None:
            try:
                for card in (getattr(win, "_card_by_node", {}) or {}).values():
                    if getattr(card, "_scene_selected_owner", None) != owner:
                        continue
                    node = getattr(card, "_node_ref", None)
                    kind = (getattr(node, "kind", "") or "").lower() if node is not None else ""
                    if kind in ("scene", "scene_assembly", "scene_outliner"):
                        scene_node = node
                        break
            except Exception:
                scene_node = None
        if scene_node is None:
            return
        kind = (getattr(scene_node, "kind", "") or "").lower()
        if kind not in ("scene", "scene_assembly", "scene_outliner"):
            return
        renderer = getattr(self, "_mgl_renderer", None) or self
        before = {}
        try:
            is_splat = False
            splat_map = getattr(renderer, "_mgl_scene_splats", None)
            if isinstance(splat_map, dict) and owner in splat_map:
                is_splat = True
            getf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
            if callable(getf):
                raw = getf(owner) or {}
                before = {"pos": tuple(raw.get("pos", (0.0, 0.0, 0.0))),
                          "rot": tuple(raw.get("rot", (0.0, 0.0, 0.0))),
                          "scl": tuple(raw.get("scl", (1.0, 1.0, 1.0)))}
        except Exception:
            before = {}
        self._xform_hist_start = {"owner": owner, "before": before, "scene_node": scene_node}

    def _commit_xform_history(self) -> None:
        data = getattr(self, "_xform_hist_start", None)
        if not isinstance(data, dict):
            return
        self._xform_hist_start = None
        owner = data.get("owner")
        scene_node = data.get("scene_node")
        before = data.get("before") or {}
        if not owner or scene_node is None:
            return
        renderer = getattr(self, "_mgl_renderer", None) or self
        after = {}
        try:
            is_splat = False
            splat_map = getattr(renderer, "_mgl_scene_splats", None)
            if isinstance(splat_map, dict) and owner in splat_map:
                is_splat = True
            getf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
            if callable(getf):
                raw = getf(owner) or {}
                after = {"pos": tuple(raw.get("pos", (0.0, 0.0, 0.0))),
                         "rot": tuple(raw.get("rot", (0.0, 0.0, 0.0))),
                         "scl": tuple(raw.get("scl", (1.0, 1.0, 1.0)))}
        except Exception:
            after = {}
        try:
            win = self.window()
            actions.record_scene_xform(win, scene_node, owner, before, after)
        except Exception:
            pass

    def set_gizmo_visible(self, visible: bool) -> None:
        try:
            self._mgl_gizmo_visible = bool(visible)
        except Exception:
            self._mgl_gizmo_visible = True
        toggle = getattr(self, "_mgl_gizmo_toggle", None)
        if toggle is not None:
            try:
                toggle.blockSignals(True)
                toggle.setChecked(bool(self._mgl_gizmo_visible))
            except Exception:
                pass
            finally:
                try:
                    toggle.blockSignals(False)
                except Exception:
                    pass
        try:
            self.update()
        except Exception:
            pass


    def _on_camlog_clicked(self) -> None:
        self._write_camera_debug_snapshot()

    def _calc_camera_world_orbit(self):
        try:
            if np is None:
                return None
            arc = getattr(self, "_mgl_arcball", None)
            if arc is None or not hasattr(arc, "Transform"):
                return None
            try:
                zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
            except Exception:
                zoom = 0.0
            rot = np.array(arc.Transform[:3, :3], dtype=np.float32)
            scale = float(np.linalg.norm(rot, ord="fro") / math.sqrt(3.0))
            if scale > 1e-6:
                rot = rot / scale
            cam_local = np.array([0.0, 0.0, zoom], dtype=np.float32)
            cam_rot = rot.T @ cam_local
            center = getattr(self, "_mgl_center", None)
            if center is not None:
                cam_rot = cam_rot + np.array(center, dtype=np.float32)
            return (float(cam_rot[0]), float(cam_rot[1]), float(cam_rot[2]))
        except Exception:
            return None

    def _log_camera_world(self, tag: str = "rmb") -> None:
        try:
            cam_orbit = self._calc_camera_world_orbit()
            center = getattr(self, "_mgl_center", None)
            zoom = getattr(self, "_mgl_camera_zoom", None)
            msg = (
                f"[CAM] {tag} cam_world_orbit={cam_orbit} "
                f"center={center} zoom={zoom}"
            )
            try:
                self._camdbg(msg)
            except Exception:
                pass
        except Exception:
            pass

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

            vm = None
            try:
                vm = (lookat * transform).astype("f4")
                lines.append("view_model:")
                lines.append(str(vm))
            except Exception:
                pass

            # camera position in world (view-space origin transformed by view inverse)
            cam_world_fixed = (0.0, 0.0, float(zoom))
            lines.append(f"cam_world_fixed: {cam_world_fixed}")

            scale_mult = None
            try:
                scale_mult = float(getattr(self, "_mgl_scale_multiplier", 1.0))
                lines.append(f"scale_multiplier: {scale_mult}")
            except Exception:
                scale_mult = None

            cam_view = None
            try:
                if np is not None and vm is not None:
                    view_np = np.array(vm, dtype=np.float32)
                    inv = np.linalg.inv(view_np)
                    cam = inv @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                    cam_view = (float(cam[0]), float(cam[1]), float(cam[2]))
                lines.append(f"cam_world_view: {cam_view}")
            except Exception as exc:
                lines.append(f"cam_world_view_error: {exc}")

            cam_orbit = None
            try:
                cam_orbit = self._calc_camera_world_orbit()
                lines.append(f"cam_world_orbit: {cam_orbit}")
            except Exception as exc:
                lines.append(f"cam_world_orbit_error: {exc}")

            # camera position expressed in object/model space (row- and column-vector conventions)
            try:
                if np is not None:
                    t = np.array(transform, dtype=np.float32)
                    inv_t = np.linalg.inv(t)
                    v = np.array([cam_world_fixed[0], cam_world_fixed[1], cam_world_fixed[2], 1.0], dtype=np.float32)
                    cam_obj_row = v @ inv_t
                    cam_obj_col = inv_t @ v
                    lines.append(
                        f"cam_obj_row: ({float(cam_obj_row[0]):.6f}, {float(cam_obj_row[1]):.6f}, {float(cam_obj_row[2]):.6f})"
                    )
                    lines.append(
                        f"cam_obj_col: ({float(cam_obj_col[0]):.6f}, {float(cam_obj_col[1]):.6f}, {float(cam_obj_col[2]):.6f})"
                    )
            except Exception as exc:
                lines.append(f"cam_obj_error: {exc}")

            try:
                if cam_orbit is not None and scale_mult not in (None, 0.0):
                    cam_orbit_scene = (
                        float(cam_orbit[0]) / float(scale_mult),
                        float(cam_orbit[1]) / float(scale_mult),
                        float(cam_orbit[2]) / float(scale_mult),
                    )
                    lines.append(f"cam_world_orbit_scene: {cam_orbit_scene}")
            except Exception as exc:
                lines.append(f"cam_world_orbit_scene_error: {exc}")

            try:
                if vm is not None and Matrix44 is not None and scale_mult not in (None, 1.0):
                    scale_mat = Matrix44.from_scale([float(scale_mult)] * 3, dtype="f4")
                    vm_scaled = (lookat * transform * scale_mat).astype("f4")
                    if np is not None:
                        inv = np.linalg.inv(np.array(vm_scaled, dtype=np.float32))
                        cam = inv @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                        cam_scaled = (float(cam[0]), float(cam[1]), float(cam[2]))
                        lines.append(f"cam_world_view_scaled: {cam_scaled}")
            except Exception as exc:
                lines.append(f"cam_world_view_scaled_error: {exc}")

            # --- splat visibility stats (CPU) ---
            try:
                cpu = getattr(self, "_mgl_splats15_cpu", None)
                if cpu is not None and np is not None and cpu.shape[0] > 0:
                    view_model = np.array(vm, dtype=np.float32) if vm is not None else None
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
            btn.setIconSize(QtCore.QSize(self._side_btn_icon, self._side_btn_icon))
            btn.setFixedSize(self._side_btn_size, self._side_btn_size)
            btn.clicked.connect(self._toggle_debug_overlay)
            self._debug_toggle_btn = btn
            self._update_debug_toggle_button()
            self._debug_toggle_btn_frame = self._wrap_side_button(self._debug_toggle_btn, "_debug_toggle_btn_frame")
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
        self._apply_side_icon_style(btn, active=self._debug_overlay)

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
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setToolTip("Copy stats")
            icon_path = Path(__file__).resolve().parents[2] / "icons" / "Copy_Icon.png"
            if icon_path.exists():
                pix = QtGui.QPixmap(str(icon_path))
                if not pix.isNull():
                    faded = QtGui.QPixmap(pix.size())
                    faded.fill(QtCore.Qt.transparent)
                    p = QtGui.QPainter(faded)
                    p.setOpacity(0.6)
                    p.drawPixmap(0, 0, pix)
                    p.end()
                    btn.setIcon(QtGui.QIcon(faded))
                    btn.setText("")
                else:
                    btn.setIcon(QtGui.QIcon(str(icon_path)))
                    btn.setText("")
            else:
                btn.setText("Copy")
            try:
                btn.setFixedSize(18, 18)
            except Exception:
                pass
            btn.setStyleSheet(
                "QToolButton{background:rgba(30,41,59,200);border:1px solid #475569;"
                "color:#e2e8f0;padding:0px;border-radius:3px;font-size:10px;}"
                "QToolButton:hover{background:rgba(51,65,85,220);}"
            )
            btn.clicked.connect(self._copy_debug_details)
            self._debug_copy_btn = btn
            btn.setVisible(self._debug_overlay)
        except Exception:
            self._debug_copy_btn = None

    def _apply_side_icon_style(self, btn: QtWidgets.QToolButton | None, active: bool = False) -> None:
        if btn is None:
            return
        bg = "#2a2f34" if active else "#22272c"
        hover = "#3a4046"
        pressed = "#1b2025"
        btn.setStyleSheet(
            "QToolButton{background:%s;border:0px;"
            "color:#1f2937;padding:0px;border-radius:3px;font-size:10px;}"
            "QToolButton:hover{background:%s;}"
            "QToolButton:pressed{background:%s;}"
            "QToolButton:checked{background:%s;}"
            "QToolButton:checked:hover{background:%s;}"
            % (bg, hover, pressed, bg, hover)
        )

    def _wrap_side_button(
        self,
        btn: QtWidgets.QToolButton | None,
        frame_attr: str,
    ) -> QtWidgets.QFrame | None:
        if btn is None:
            return None
        frame = getattr(self, frame_attr, None)
        if frame is None:
            frame = QtWidgets.QFrame(self)
            frame.setObjectName("GLSideIconFrame")
            frame.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            setattr(self, frame_attr, frame)
        else:
            frame.setParent(self)
        frame.setStyleSheet(
            "QFrame#GLSideIconFrame{background:#555b61;border:1px solid #0f1418;border-radius:4px;}"
        )
        inner = max(1, int(self._side_btn_size - (2 * self._side_btn_inner_pad)))
        icon = min(self._side_btn_icon, inner)
        btn.setParent(frame)
        btn.move(self._side_btn_inner_pad, self._side_btn_inner_pad)
        btn.setFixedSize(inner, inner)
        btn.setIconSize(QtCore.QSize(icon, icon))
        frame.setFixedSize(self._side_btn_size, self._side_btn_size)
        frame.show()
        btn.show()
        return frame

    def _build_camera_orbit_button(self) -> None:
        try:
            btn = QtWidgets.QToolButton(self)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setIconSize(QtCore.QSize(self._side_btn_icon, self._side_btn_icon))
            btn.setFixedSize(self._side_btn_size, self._side_btn_size)
            btn.clicked.connect(self._on_camera_orbit_toggled)
            self._cam_orbit_btn = btn
            self._update_camera_orbit_button()
            self._cam_orbit_btn_frame = self._wrap_side_button(self._cam_orbit_btn, "_cam_orbit_btn_frame")
            btn.show()
        except Exception:
            self._cam_orbit_btn = None

    def _load_camera_orbit_icons(self) -> None:
        if self._cam_orbit_icon_locked is not None or self._cam_orbit_icon_free is not None:
            return
        icon_locked = None
        icon_free = None
        try:
            root = Path(__file__).resolve().parents[2]
            locked_path = root / "icons" / "CameraCtrl_On_Icon.png"
            free_path = root / "icons" / "CameraCtrl_Off_Icon.png"
            if locked_path.exists():
                icon_locked = QtGui.QIcon(str(locked_path))
            if free_path.exists():
                icon_free = QtGui.QIcon(str(free_path))
        except Exception:
            icon_locked = None
            icon_free = None
        self._cam_orbit_icon_locked = icon_locked
        self._cam_orbit_icon_free = icon_free

    def _update_camera_orbit_button(self) -> None:
        btn = getattr(self, "_cam_orbit_btn", None)
        if btn is None:
            return
        locked = bool(getattr(self, "_mgl_orbit_locked", True))
        btn.setChecked(locked)
        self._load_camera_orbit_icons()
        if locked:
            btn.setToolTip("Camera Orbit: Locked (no roll)")
            icon = self._cam_orbit_icon_locked
            fallback = "Lock"
        else:
            btn.setToolTip("Camera Orbit: Free (roll)")
            icon = self._cam_orbit_icon_free
            fallback = "Free"
        if icon is not None:
            btn.setIcon(icon)
            btn.setText("")
        else:
            btn.setText(fallback)
        self._apply_side_icon_style(btn, active=locked)

    def _on_camera_orbit_toggled(self, checked=None) -> None:
        if checked is None:
            checked = bool(getattr(self, "_cam_orbit_btn", None) and self._cam_orbit_btn.isChecked())
        self._mgl_orbit_locked = bool(checked)
        self._mgl_orbit_dragging = False
        self._mgl_orbit_last_pos = None
        if self._mgl_orbit_locked:
            self._sync_locked_orbit_from_arcball()
            self._apply_locked_orbit()
        else:
            try:
                self._mgl_arcball_sync_after_set_transform()
            except Exception:
                pass
        self._update_camera_orbit_button()
        try:
            self.update()
        except Exception:
            pass

    def _build_fly_mode_button(self) -> None:
        try:
            btn = QtWidgets.QToolButton(self)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setIconSize(QtCore.QSize(self._side_btn_icon, self._side_btn_icon))
            btn.setFixedSize(self._side_btn_size, self._side_btn_size)
            btn.clicked.connect(self._on_fly_mode_toggled)
            self._fly_mode_btn = btn
            self._update_fly_mode_button()
            self._fly_mode_btn_frame = self._wrap_side_button(self._fly_mode_btn, "_fly_mode_btn_frame")
            btn.show()
        except Exception:
            self._fly_mode_btn = None

    def _load_fly_mode_icons(self) -> None:
        if self._fly_mode_icon_on is not None or self._fly_mode_icon_off is not None:
            return
        icon_on = None
        icon_off = None
        try:
            root = Path(__file__).resolve().parents[2]
            on_path = root / "icons" / "FlyModeJoystick_On_Icon.png"
            off_path = root / "icons" / "FlyModeJoystick_Off_Icon.png"
            if on_path.exists():
                icon_on = QtGui.QIcon(str(on_path))
            if off_path.exists():
                icon_off = QtGui.QIcon(str(off_path))
        except Exception:
            icon_on = None
            icon_off = None
        self._fly_mode_icon_on = icon_on
        self._fly_mode_icon_off = icon_off

    def _update_fly_mode_button(self) -> None:
        btn = getattr(self, "_fly_mode_btn", None)
        if btn is None:
            return
        enabled = bool(getattr(self, "_fly_mode_enabled", False))
        btn.setChecked(enabled)
        self._load_fly_mode_icons()
        if enabled:
            btn.setToolTip("Fly Mode: On")
            icon = self._fly_mode_icon_on
            fallback = "Fly"
        else:
            btn.setToolTip("Fly Mode: Off")
            icon = self._fly_mode_icon_off
            fallback = "Fly"
        if icon is not None:
            btn.setIcon(icon)
            btn.setText("")
        else:
            btn.setText(fallback)
        self._apply_side_icon_style(btn, active=enabled)

    def _on_fly_mode_toggled(self, checked=None) -> None:
        if checked is None:
            checked = bool(getattr(self, "_fly_mode_btn", None) and self._fly_mode_btn.isChecked())
        enabled = bool(checked)
        self._fly_mode_enabled = enabled
        if enabled:
            self._orbit_cam_enabled = False
            self._fps_camera_active = True
            self._fps_nav_active = True
            self._fps_nav_last_t = time.perf_counter()
            self._fps_nav_keys = set()
            self._fps_nav_look_last_pos = None
            try:
                self._fps_cam_sync_from_orbit()
            except Exception:
                pass
        else:
            self._orbit_cam_enabled = True
            try:
                if bool(getattr(self, "_fps_camera_active", False)):
                    self._fps_cam_sync_orbit_from_camera()
            except Exception:
                pass
            self._fps_camera_active = False
            self._fps_nav_active = False
            self._fps_nav_keys = set()
            self._fps_nav_look_last_pos = None
            self._fps_nav_cursor_anchor = None
            self._fps_nav_warping = False
            self._fps_nav_boost = False
        self._update_fly_mode_button()
        try:
            self.update()
        except Exception:
            pass

    def _sync_locked_orbit_from_arcball(self) -> None:
        arc = getattr(self, "_mgl_arcball", None)
        if arc is None or np is None or not hasattr(arc, "Transform"):
            return
        try:
            t = np.array(arc.Transform, dtype=np.float32)
            if t.shape != (4, 4):
                return
            rmat = t[:3, :3].astype(np.float32, copy=False)
            umat, _, vmat = np.linalg.svd(rmat)
            r_norm = (umat @ vmat).astype(np.float32, copy=False)
            if np.linalg.det(r_norm) < 0:
                umat[:, -1] *= -1.0
                r_norm = (umat @ vmat).astype(np.float32, copy=False)
            right = r_norm[:, 0]
            up = r_norm[:, 1]
            f = r_norm[:, 2]
            dist = float(np.linalg.norm(f))
            if dist < 1e-6:
                return
            rn = float(np.linalg.norm(right))
            if rn > 1e-6:
                yaw = math.atan2(float(-right[2]), float(right[0]))
            else:
                yaw = math.atan2(float(f[0]), float(f[2]))
            fh = math.sqrt(float(f[0] * f[0] + f[2] * f[2]))
            pitch = math.atan2(float(f[1]), float(fh))
            if float(up[1]) < 0.0:
                if pitch >= 0.0:
                    pitch = math.pi - pitch
                else:
                    pitch = -math.pi - pitch
            self._mgl_orbit_yaw = yaw
            self._mgl_orbit_pitch = pitch
        except Exception:
            pass

    def _apply_locked_orbit(self) -> None:
        arc = getattr(self, "_mgl_arcball", None)
        if arc is None or np is None:
            return
        yaw = float(getattr(self, "_mgl_orbit_yaw", 0.0))
        pitch = float(getattr(self, "_mgl_orbit_pitch", 0.0))
        cy = math.cos(yaw)
        sy = math.sin(yaw)
        cp = math.cos(pitch)
        sp = math.sin(pitch)
        forward = np.array([sy * cp, sp, cy * cp], dtype=np.float32)
        right = np.array([cy, 0.0, -sy], dtype=np.float32)
        rn = float(np.linalg.norm(right))
        if rn < 1e-6:
            right = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        else:
            right /= rn
        up = np.cross(forward, right)
        un = float(np.linalg.norm(up))
        if un < 1e-6:
            up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        else:
            up /= un
        right = np.cross(up, forward)
        rn = float(np.linalg.norm(right))
        if rn > 1e-6:
            right /= rn
        rot = np.stack([right, up, forward], axis=1)
        try:
            arc.Transform = arc._set_rotation(arc.Transform, rot)
        except Exception:
            return
        try:
            self._mgl_arcball_sync_after_set_transform()
        except Exception:
            pass

    def _build_grid_button(self) -> None:
        try:
            btn = QtWidgets.QToolButton(self)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setIconSize(QtCore.QSize(self._side_btn_icon, self._side_btn_icon))
            btn.setFixedSize(self._side_btn_size, self._side_btn_size)
            btn.clicked.connect(self._on_grid_button_toggled)
            self._grid_btn = btn
            self._update_grid_button()
            self._grid_btn_frame = self._wrap_side_button(self._grid_btn, "_grid_btn_frame")
            try:
                self._on_mgl_grid_toggled(bool(getattr(self, "_mgl_grid_visible", False)))
            except Exception:
                pass
            btn.show()
        except Exception:
            self._grid_btn = None

    def _load_grid_icons(self) -> None:
        if self._grid_icon_on is not None or self._grid_icon_off is not None:
            return
        icon_on = None
        icon_off = None
        try:
            root = Path(__file__).resolve().parents[2]
            on_path = root / "icons" / "grid_on_icon.png"
            off_path = root / "icons" / "grid_off_icon.png"
            if on_path.exists():
                icon_on = QtGui.QIcon(str(on_path))
            if off_path.exists():
                icon_off = QtGui.QIcon(str(off_path))
        except Exception:
            icon_on = None
            icon_off = None
        self._grid_icon_on = icon_on
        self._grid_icon_off = icon_off

    def _update_grid_button(self) -> None:
        btn = getattr(self, "_grid_btn", None)
        if btn is None:
            return
        visible = bool(getattr(self, "_mgl_grid_visible", False))
        btn.setChecked(visible)
        self._load_grid_icons()
        if visible:
            btn.setToolTip("Grid: On")
            icon = self._grid_icon_on
            fallback = "Grid"
        else:
            btn.setToolTip("Grid: Off")
            icon = self._grid_icon_off
            fallback = "Grid"
        if icon is not None:
            btn.setIcon(icon)
            btn.setText("")
        else:
            btn.setText(fallback)
        self._apply_side_icon_style(btn, active=visible)

    def _on_grid_button_toggled(self, checked=None) -> None:
        if checked is None:
            checked = bool(getattr(self, "_grid_btn", None) and self._grid_btn.isChecked())
        try:
            self._mgl_grid_visible = bool(checked)
        except Exception:
            pass
        try:
            self._on_mgl_grid_toggled(bool(checked))
        except Exception:
            try:
                self.update()
            except Exception:
                pass
        self._update_grid_button()

    def _build_zoom_mode_button(self) -> None:
        try:
            btn = QtWidgets.QToolButton(self)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setCheckable(True)
            btn.setIconSize(QtCore.QSize(self._side_btn_icon, self._side_btn_icon))
            btn.setFixedSize(self._side_btn_size, self._side_btn_size)
            btn.clicked.connect(self._on_zoom_mode_toggled)
            self._zoom_mode_btn = btn
            self._update_zoom_mode_button()
            self._zoom_mode_btn_frame = self._wrap_side_button(self._zoom_mode_btn, "_zoom_mode_btn_frame")
            btn.show()
        except Exception:
            self._zoom_mode_btn = None

    def _load_zoom_mode_icons(self) -> None:
        if self._zoom_mode_icon_on is not None or self._zoom_mode_icon_off is not None:
            return
        icon_on = None
        icon_off = None
        try:
            root = Path(__file__).resolve().parents[2]
            on_path = root / "icons" / "GizmoZoom_On_Icon.png"
            off_path = root / "icons" / "GizmoZoom_Off_Icon.png"
            if on_path.exists():
                icon_on = QtGui.QIcon(str(on_path))
            if off_path.exists():
                icon_off = QtGui.QIcon(str(off_path))
        except Exception:
            icon_on = None
            icon_off = None
        self._zoom_mode_icon_on = icon_on
        self._zoom_mode_icon_off = icon_off

    def _update_zoom_mode_button(self) -> None:
        btn = getattr(self, "_zoom_mode_btn", None)
        if btn is None:
            return
        infinite = bool(getattr(self, "_mgl_zoom_infinite", True))
        btn.setChecked(infinite)
        self._load_zoom_mode_icons()
        if infinite:
            btn.setToolTip("Zoom Mode: Infinite")
            icon = self._zoom_mode_icon_on
            fallback = "Zoom+"
        else:
            btn.setToolTip("Zoom Mode: Legacy")
            icon = self._zoom_mode_icon_off
            fallback = "Zoom"
        if icon is not None:
            btn.setIcon(icon)
            btn.setText("")
        else:
            btn.setText(fallback)
        self._apply_side_icon_style(btn, active=infinite)

    def _on_zoom_mode_toggled(self, checked=None) -> None:
        if checked is None:
            checked = bool(getattr(self, "_zoom_mode_btn", None) and self._zoom_mode_btn.isChecked())
        self._mgl_zoom_infinite = bool(checked)
        self._update_zoom_mode_button()
        try:
            self.update()
        except Exception:
            pass

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
            try:
                if self._frame_selected_owner():
                    self.update()
                    return
            except Exception:
                pass
            self._mgl_frame_camera()
            self.update()
            return
        if self._use_example_pipeline:
            self._frame_example_camera()
            self.update()
            return
        self._reset_camera()
        self.update()

    def _frame_selected_owner(self) -> bool:
        owner = getattr(self, "_xform_gizmo_owner", None)
        if not owner:
            return False
        renderer = getattr(self, "_mgl_renderer", None) or self
        get_bounds = getattr(renderer, "get_scene_owner_bounds", None)
        if not callable(get_bounds):
            return False
        if np is None:
            return False
        b = get_bounds(owner)
        mins = None
        maxs = None
        if isinstance(b, (list, tuple)) and len(b) >= 2:
            bmin, bmax = b
            if bmin is not None and bmax is not None:
                try:
                    mins = np.array(bmin, dtype=np.float32)
                    maxs = np.array(bmax, dtype=np.float32)
                    if mins.shape[0] < 3 or maxs.shape[0] < 3:
                        mins = None
                        maxs = None
                except Exception:
                    mins = None
                    maxs = None

        if mins is None or maxs is None:
            try:
                is_splat = str(getattr(self, "_xform_gizmo_owner_kind", "")).lower() == "splat"
                get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None) if is_splat else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                if callable(get_xf):
                    xf = get_xf(owner) or {}
                    pos = xf.get("pos", (0.0, 0.0, 0.0))
                    mins = np.array(pos, dtype=np.float32)
                    maxs = np.array(pos, dtype=np.float32)
            except Exception:
                mins = None
                maxs = None
        if mins is None or maxs is None:
            return False

        center = (mins + maxs) * 0.5
        extent = (maxs - mins) * 0.5
        radius = float(max(extent[0], extent[1], extent[2]))
        if radius <= 1e-6:
            radius = 0.5
        inv_scale = 1.0
        try:
            arc = getattr(renderer, "_mgl_arcball", None)
            if arc is not None and np is not None and hasattr(arc, "Transform"):
                t = np.array(arc.Transform, dtype=np.float32)
                if t.shape == (4, 4):
                    sx = float(np.linalg.norm(t[0, :3]))
                    sy = float(np.linalg.norm(t[1, :3]))
                    sz = float(np.linalg.norm(t[2, :3]))
                    s_avg = (sx + sy + sz) / 3.0
                    if s_avg > 1e-6:
                        inv_scale = s_avg
        except Exception:
            inv_scale = 1.0
        fov = float(getattr(renderer, "_mgl_fov", 60.0))
        dist = (radius * inv_scale) / max(1e-6, math.tan(math.radians(fov * 0.5)))
        base_zoom = max(0.1, float(dist) * 1.2)
        try:
            renderer._mgl_center = center.astype("f4")
        except Exception:
            renderer._mgl_center = center
        try:
            renderer._mgl_camera_zoom = float(base_zoom) * max(0.01, float(getattr(renderer, "_mgl_scale_multiplier", 1.0)))
        except Exception:
            renderer._mgl_camera_zoom = float(base_zoom)
        return True

    def _ray_from_mouse(self, pos: QtCore.QPoint):
        if not self._use_moderngl or np is None:
            return None
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            Pn = getattr(renderer, "_mgl_pick_proj", None)
            Vn = getattr(renderer, "_mgl_pick_view", None)
            Mn = getattr(renderer, "_mgl_pick_model", None)
            if Pn is None or Vn is None or Mn is None:
                return None

            dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
            vw = float(self.width()) * dpr
            vh = float(self.height()) * dpr
            px = float(pos.x()) * dpr
            py = float(pos.y()) * dpr

            PV = (
                np.asarray(Pn, dtype=np.float32)
                @ np.asarray(Vn, dtype=np.float32)
                @ np.asarray(Mn, dtype=np.float32)
            ).astype(np.float32)
            invPV = np.linalg.inv(PV)

            x = (2.0 * (px / max(1.0, vw))) - 1.0
            y = 1.0 - (2.0 * (py / max(1.0, vh)))
            near = np.array([x, y, -1.0, 1.0], dtype=np.float32)
            far = np.array([x, y, 1.0, 1.0], dtype=np.float32)
            pN = invPV @ near
            pF = invPV @ far
            pN = pN[:3] / pN[3]
            pF = pF[:3] / pF[3]
            ray_o = pN
            ray_d = pF - pN
            rn = float(np.linalg.norm(ray_d))
            if rn <= 1e-8:
                return None
            ray_d /= rn
            return ray_o.astype("f4"), ray_d.astype("f4")
        except Exception:
            return None
    def _on_snapgrab_clicked(self) -> None:
        paused = False
        image = None
        try:
            if hasattr(self, "makeCurrent"):
                self.makeCurrent()

            # force GPU completion (driver stability)
            try:
                ctx = self.context()
                if ctx is not None:
                    f = ctx.functions()
                    if f is not None and hasattr(f, "glFinish"):
                        f.glFinish()
            except Exception:
                pass
            
            # ensure we capture a freshly rendered frame
            try:
                self.update()
                self.repaint()
                QtWidgets.QApplication.processEvents()
            except Exception:
                pass

            image = self.grabFramebuffer()

        finally:
            try:
                if hasattr(self, "doneCurrent"):
                    self.doneCurrent()
            except Exception:
                pass
            if paused:
                self._render_paused = False
                self.update()

        if image is None or image.isNull():
            return

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
            # Clear any prior scene assets so picking doesn't hit hidden scene models.
            try:
                self._clear_scene_asset_state()
            except Exception:
                pass
            try:
                if hasattr(self, "clear_procedural_texture_provider"):
                    self.clear_procedural_texture_provider()
            except Exception:
                pass

            # Keep scale fixed to 1.0 for model preview loads.
            try:
                self._mgl_scale_multiplier = 1.0
                lab = getattr(self, "_example_scale_label", None)
                if lab is not None:
                    lab.setText("Scale 1.00x")
                sld = getattr(self, "_example_scale_slider", None)
                if sld is not None:
                    try:
                        sld.blockSignals(True)
                        sld.setValue(100)
                    finally:
                        sld.blockSignals(False)
            except Exception:
                pass

            self._mgl_load_mesh(model_path)
            if texture_str:
                self._apply_texture_path(texture_str)
                try:
                    self._mgl_uv_bg_path = texture_str
                    self._mgl_uv_cache = None
                except Exception:
                    pass
            else:
                try:
                    tex_list = getattr(self, "_mgl_texture_paths", None)
                    if isinstance(tex_list, list) and tex_list:
                        self._mgl_uv_bg_path = str(tex_list[0])
                        self._mgl_uv_cache = None
                    else:
                        self._mgl_uv_bg_path = ""
                        self._mgl_uv_cache = None
                except Exception:
                    pass

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

    def set_procedural_texture_provider(self, provider, label: str = "Procedural") -> None:
        if not self._use_moderngl:
            return
        if provider is None:
            self.clear_procedural_texture_provider()
            return
        self._mgl_proc_provider = provider
        self._mgl_proc_label = (label or "procedural").strip() or "procedural"
        self._mgl_proc_rev = -1
        self._mgl_proc_gpu_enabled = False
        self._mgl_proc_gpu_state = None
        img = None
        try:
            gpu_state = getattr(provider, "gpu_state", None)
            if callable(gpu_state):
                gpu_state = gpu_state()
            if isinstance(gpu_state, dict) and gpu_state:
                self._mgl_proc_gpu_enabled = True
                self._mgl_proc_gpu_state = gpu_state
        except Exception:
            self._mgl_proc_gpu_enabled = False
            self._mgl_proc_gpu_state = None
        if self._mgl_proc_gpu_enabled:
            self._mgl_texture_override = False
            self._mgl_texture_paths = []
            self._mgl_uv_bg_path = ""
            self._mgl_uv_cache = None
            self.update()
            return
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
        if img is not None and not isinstance(img, QtGui.QImage):
            try:
                img = self._mgl_qimage_from_texture(img)
            except Exception:
                img = None
        if img is not None and not img.isNull():
            try:
                self.makeCurrent()
                self._mgl_upload_texture(img, self._mgl_proc_label)
                self._mgl_texture_override = True
                self._mgl_texture_paths = [self._mgl_proc_label]
                self._mgl_uv_bg_path = ""
                self._mgl_uv_cache = None
            except Exception:
                pass
            finally:
                try:
                    self.doneCurrent()
                except Exception:
                    pass
        self.update()

    def clear_procedural_texture_provider(self) -> None:
        try:
            if getattr(self, "_mgl_texture", None) is not None:
                try:
                    self._mgl_texture.release()
                except Exception:
                    pass
        except Exception:
            pass
        self._mgl_texture = None
        self._mgl_texture_path = ""
        self._mgl_texture_paths = []
        self._mgl_texture_override = False
        self._mgl_proc_provider = None
        self._mgl_proc_label = ""
        self._mgl_proc_rev = -1
        self._mgl_proc_gpu_enabled = False
        self._mgl_proc_gpu_state = None

    def _clear_scene_asset_state(self) -> None:
        if not self._use_moderngl:
            return
        try:
            self._mgl_clear_scene_models()
        except Exception:
            pass
        try:
            self._mgl_disable_splats()
        except Exception:
            pass
        for name in (
            "_mgl_scene_visibility",
            "_mgl_scene_splats",
            "_mgl_scene_bounds_by_owner",
            "_mgl_scene_mesh_bounds_by_owner",
            "_mgl_scene_splats_world",
            "_mgl_scene_splats_bounds_local",
            "_mgl_scene_splat_bounds_by_owner",
            "_mgl_scene_xforms_by_owner",
            "_mgl_scene_splat_xforms_by_owner",
            "_mgl_scene_xform_offset_by_owner",
        ):
            try:
                setattr(self, name, {})
            except Exception:
                pass
        try:
            self._mgl_pending_visibility = {}
            self._mgl_visibility_dirty = False
        except Exception:
            pass
        try:
            self._xform_gizmo_owner = None
            self._xform_gizmo_owner_kind = None
            self._xform_gizmo_pos_locked = False
            self._xform_gizmo_pos = (0.0, 0.0, 0.0)
        except Exception:
            pass

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
            try:
                self._mgl_scene_xform_offset_by_owner = {}
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
            try:
                if hasattr(self, "clear_procedural_texture_provider"):
                    self.clear_procedural_texture_provider()
            except Exception:
                pass
            # Seed xforms from saved workflow data (if present on assets)
            try:
                # Track owners that should use offset-based xforms (keep baked coords stable)
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
                if offset_map:
                    self._mgl_scene_xform_offset_by_owner = offset_map
                elif isinstance(getattr(self, "_mgl_scene_xform_offset_by_owner", None), dict):
                    self._mgl_scene_xform_offset_by_owner = {}
                try:
                    renderer = getattr(self, "_mgl_renderer", None)
                    if renderer is not None and renderer is not self:
                        setattr(renderer, "_mgl_scene_xform_offset_by_owner", dict(self._mgl_scene_xform_offset_by_owner))
                except Exception:
                    pass

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
                    if entry.get("xform_offset"):
                        try:
                            pos = xf.get("pos", (0.0, 0.0, 0.0))
                            rot = xf.get("rot", (0.0, 0.0, 0.0))
                            scl = xf.get("scl", (1.0, 1.0, 1.0))
                            if (
                                all(abs(float(v)) < 1e-6 for v in (pos or (0.0, 0.0, 0.0)))
                                and all(abs(float(v)) < 1e-6 for v in (rot or (0.0, 0.0, 0.0)))
                                and all(abs(float(v) - 1.0) < 1e-6 for v in (scl or (1.0, 1.0, 1.0)))
                            ):
                                continue
                        except Exception:
                            pass
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

    def set_scene_asset_uv_overlay(self, owner: str | None) -> None:
        if not self._use_moderngl:
            return
        key = str(owner or "").strip()
        renderer = getattr(self, "_mgl_renderer", None) or self
        uvs = None
        uvs_map = None
        bg_path = ""
        try:
            uvs_map = getattr(renderer, "_mgl_scene_uvs_by_owner", None)
            if isinstance(uvs_map, dict) and key:
                uvs = uvs_map.get(key)
                if uvs is None:
                    key_lower = key.lower()
                    for k, v in uvs_map.items():
                        if str(k).strip().lower() == key_lower:
                            uvs = v
                            break
        except Exception:
            uvs = None
        try:
            tex_map = getattr(renderer, "_mgl_scene_tex_by_owner", None)
            if isinstance(tex_map, dict) and key:
                bg_path = tex_map.get(key, "") or ""
                if not bg_path:
                    key_lower = key.lower()
                    for k, v in tex_map.items():
                        if str(k).strip().lower() == key_lower:
                            bg_path = str(v or "")
                            break
        except Exception:
            bg_path = ""
        if uvs is None and key:
            try:
                scene = getattr(renderer, "_mgl_scene", None)
                path = None
                if scene is not None:
                    for item in scene.items():
                        payload = getattr(item, "payload", {}) or {}
                        owner_key = payload.get("owner") or payload.get("node")
                        if owner_key and str(owner_key).strip().lower() == key.lower():
                            path = payload.get("path")
                            if path:
                                break
                if path:
                    p = Path(str(path))
                    if p.exists():
                        ext = p.suffix.lower()
                        try:
                            from echograph.ui.gl_loaders import (
                                load_obj_mesh_arrays,
                                load_gltf_mesh_arrays,
                                load_fbx_mesh_arrays_pyassimp,
                            )
                            import numpy as _np
                        except Exception:
                            load_obj_mesh_arrays = None
                            load_gltf_mesh_arrays = None
                            load_fbx_mesh_arrays_pyassimp = None
                            _np = None

                        if ext == ".obj" and load_obj_mesh_arrays is not None:
                            try:
                                _pts, _nrm, uvs = load_obj_mesh_arrays(p)
                            except Exception:
                                uvs = None
                        elif ext in (".gltf", ".glb") and load_gltf_mesh_arrays is not None:
                            try:
                                mesh_arrays = load_gltf_mesh_arrays(p)
                                if mesh_arrays is not None and mesh_arrays.submeshes:
                                    if _np is not None:
                                        uvs = _np.concatenate([s.uvs for s in mesh_arrays.submeshes], axis=0)
                                elif mesh_arrays is not None:
                                    uvs = mesh_arrays.uvs
                            except Exception:
                                uvs = None
                        elif ext == ".fbx" and load_fbx_mesh_arrays_pyassimp is not None:
                            try:
                                mesh_arrays = load_fbx_mesh_arrays_pyassimp(p)
                                if mesh_arrays is not None and mesh_arrays.submeshes:
                                    if _np is not None:
                                        uvs = _np.concatenate([s.uvs for s in mesh_arrays.submeshes], axis=0)
                                elif mesh_arrays is not None:
                                    uvs = mesh_arrays.uvs
                            except Exception:
                                uvs = None

                        if uvs is not None:
                            try:
                                if not isinstance(uvs_map, dict):
                                    uvs_map = {}
                                    renderer._mgl_scene_uvs_by_owner = uvs_map
                                uvs_map[key] = uvs.astype("f4").reshape(-1, 2)
                                uvs = uvs_map[key]
                            except Exception:
                                pass
            except Exception:
                pass
        if not bg_path:
            try:
                if bool(getattr(renderer, "_mgl_texture_override", False)):
                    bg_path = str(getattr(renderer, "_mgl_texture_path", "") or "")
                if not bg_path:
                    tex_list = getattr(renderer, "_mgl_texture_paths", None)
                    if isinstance(tex_list, list) and tex_list:
                        bg_path = str(tex_list[0])
            except Exception:
                bg_path = ""
        if bg_path != getattr(self, "_mgl_uv_bg_path", ""):
            self._mgl_uv_bg_path = bg_path
            self._mgl_uv_cache = None
        try:
            renderer._mgl_set_uv_overlay(uvs)
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass

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

        # preserve per-owner transforms when renaming
        for attr in (
            "_mgl_scene_xforms_by_owner",
            "_mgl_scene_splat_xforms_by_owner",
            "_mgl_scene_xform_offset_by_owner",
        ):
            try:
                d = getattr(self, attr, None)
                if isinstance(d, dict) and old_key in d:
                    d[new_key] = d.pop(old_key)
            except Exception:
                pass

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
        return _ex_cube_data(self)

    def _example_grid_data(self, extent: float = 12.0, step: float = 1.0) -> List[float]:
        return _ex_grid_data(self, extent=extent, step=step)

    def _upload_example_grid(self, vertices: List[float]) -> bool:
        return _ex_upload_example_grid(self, vertices)

    def _build_example_program(
        self,
        vertex_src: str,
        fragment_src: str,
        bind_locations: Optional[Dict[str, int]] = None,
    ) -> Optional[QtGui.QOpenGLShaderProgram]:
        return _ex_build_example_program(self, vertex_src, fragment_src, bind_locations)

    def _float_bytes(self, values: List[float]) -> QtCore.QByteArray:
        return float_bytes(QtCore, array, values)

    def _upload_example_vertices(self, vertices: List[float]) -> bool:
        return _ex_upload_example_vertices(self, vertices)

    def _init_example_pipeline(self) -> None:
        return _ex_init_example_pipeline(self)

    def _queue_example_model(self, path: Path) -> None:
        return _ex_queue_example_model(self, path)

    def _apply_example_bounds(self, bounds: Tuple[float, float, float, float, float, float]) -> None:
        return _ex_apply_example_bounds(self, bounds)

    def _example_scaled_extent(self) -> float:
        return _ex_example_scaled_extent(self)

    def _update_example_transform(self) -> None:
        return _ex_update_example_transform(self)

    def _frame_example_camera(self) -> None:
        return _ex_frame_example_camera(self)
    
    def _apply_example_pending(self) -> None:
        return _ex_apply_example_pending(self)

    def _update_example_projection(self) -> None:
        return _ex_update_example_projection(self)

    def _update_example_camera_basis(self) -> None:
        return _ex_update_example_camera_basis(self)

    def _example_view_matrix(self) -> QtGui.QMatrix4x4:
        return _ex_example_view_matrix(self)

    def _sync_example_gizmo(self) -> None:
        return _ex_sync_example_gizmo(self)

    def _example_orbit(self, delta: QtCore.QPointF) -> None:
        return _ex_example_orbit(self, delta)

    def _example_pan(self, delta: QtCore.QPointF) -> None:
        return _ex_example_pan(self, delta)    

    def _example_zoom(self, delta_steps: float) -> None:
        return _ex_example_zoom(self, delta_steps)

    def _paint_example(self) -> None:
        return _paint_example(self)

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
            # MSAA (helps jaggies on lines)
            try:
                self._gl.glEnable(0x809D)  # GL_MULTISAMPLE
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
        return _paint_gl(self)

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
                panel_left = 10.0
                panel_top = 10.0
                toggle_btn = getattr(self, "_debug_toggle_btn", None)
                if toggle_btn is not None and toggle_btn.isVisible():
                    panel_left = (
                        toggle_btn.geometry().right()
                        + float(self._side_btn_gap)
                        + float(self._side_btn_margin)
                        + 4.0
                    )
                    panel_top = toggle_btn.geometry().top()
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
                    ip.setPen(QtGui.QColor(51, 65, 85, 160))
                    ip.setBrush(QtGui.QColor(15, 23, 42, 180))
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
                desired_left = panel_left
                panel_left = max(desired_left, min(panel_left, float(self.width()) - panel_w - 10.0))
                panel_top = max(10.0, min(panel_top, float(self.height()) - panel_h - 10.0))
                copy_btn = getattr(self, "_debug_copy_btn", None)
                if copy_btn is not None and copy_btn.isVisible():
                    btn_size = 18
                    try:
                        copy_btn.setFixedSize(btn_size, btn_size)
                        copy_btn.setIconSize(QtCore.QSize(btn_size - 4, btn_size - 4))
                    except Exception:
                        pass
                    btn_x = panel_left + panel_w - btn_size - 4.0
                    btn_y = panel_top + 4.0
                    copy_btn.setGeometry(int(btn_x), int(btn_y), btn_size, btn_size)
                painter.drawImage(QtCore.QPointF(panel_left, panel_top), self._debug_overlay_cache)
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
                dist_raw = max(dist_raw, 1e-6)

                try:
                    sm = float(getattr(renderer, "_mgl_scale_multiplier", 1.0))
                except Exception:
                    sm = 1.0

                # Keep gizmo size consistent across scene scale changes.
                dist = dist_raw * sm

                # Match smoketest derivation for stable pixel size.
                ring_r = float(rot_shared.gizmo_radius)
                target_ring_px = float(rot_shared.xyz_ring_radius_px())
                if ring_r <= 1e-6:
                    return

                # Compensate for scene normalization scale baked into Mn.
                scene_scale = 1.0
                try:
                    sx = float(np.linalg.norm(Mn[:3, 0]))
                    sy = float(np.linalg.norm(Mn[:3, 1]))
                    sz = float(np.linalg.norm(Mn[:3, 2]))
                    scene_scale = (sx + sy + sz) / 3.0
                    if scene_scale <= 1e-6:
                        scene_scale = 1.0
                except Exception:
                    scene_scale = 1.0

                s = (target_ring_px * 2.0 * dist) / (vh * proj_y * ring_r * scene_scale)
                s = max(1e-6, min(1000.0, float(s)))

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

        # Compute view_dir_local in the SAME gizmo-local space used by mvp = Pn @ Vn @ Mn @ (T @ R @ S)
        view_dir_local = QtGui.QVector3D(0.0, 0.0, 1.0)
        try:
            Vn = np.asarray(V, dtype=np.float32)
            Mn = np.asarray(M, dtype=np.float32)

            # camera position in the same "world" space that Vn views
            invV = np.linalg.inv(Vn)
            cam4 = invV @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
            cw = float(cam4[3]) if abs(float(cam4[3])) > 1e-6 else 1.0
            cam_world = np.array([float(cam4[0]) / cw, float(cam4[1]) / cw, float(cam4[2]) / cw, 1.0], dtype=np.float32)

            # gizmo local -> world is (Mn @ (T @ R))  (ignore S for direction)
            TR = (T @ R).astype(np.float32)
            G = (Mn @ TR).astype(np.float32)

            invG = np.linalg.inv(G)
            cam_local4 = invG @ cam_world

            lx, ly, lz = float(cam_local4[0]), float(cam_local4[1]), float(cam_local4[2])
            ln = (lx * lx + ly * ly + lz * lz) ** 0.5
            if ln > 1e-6:
                view_dir_local = QtGui.QVector3D(lx / ln, ly / ln, lz / ln)

            # cache so draw and pick stay consistent
            self._rot_shared_view_dir_local = view_dir_local
        except Exception:
            pass



        # XYZ rings (new shared gizmo)
        clip_val = 1.0 if bool(getattr(self, "_rot_clip_enabled", True)) else 0.0
        back_clip_cos = -math.cos(math.pi * float(getattr(self, "_rot_clip_frac", 0.55)))

        rot_shared.draw_xyz_core_2d(
            widget=self,
            viewport_w=self.width(),
            viewport_h=self.height(),
            mvp=mvp,
            view_dir_local=view_dir_local,
            back_clip_cos=float(back_clip_cos),
            clip_enabled=(clip_val > 0.5),
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
                back_clip_cos=float(back_clip_cos),
                clip_enabled=(clip_val > 0.5),
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
            center_grab_r_px = max(10.0, float(rot_shared.xyz_ring_radius_px()))
            hover_center = (d <= float(center_grab_r_px))

        # Draw center + view ring
        target_ring_px = float(rot_shared.xyz_ring_radius_px())
        rot_shared.draw_center_disc_2d(widget=self, center=center, radius_px=target_ring_px, hovered=bool(hover_center))
        rot_shared.draw_view_ring_2d(widget=self, center=center, hovered=bool(hover_view or rot_shared.drag_view))

        # Draw halo on hovered axis (or active drag axis)
        if hover_axis in ("x", "y", "z"):
            rot_shared.draw_hover_halo_2d(
                widget=self,
                axis=str(hover_axis),
                viewport_w=self.width(),
                viewport_h=self.height(),
                mvp=mvp,
                view_dir_local=view_dir_local,
                back_clip_cos=float(back_clip_cos),
                clip_enabled=(clip_val > 0.5),
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

    def _get_owner_scl(self, owner: str):
        renderer = getattr(self, "_mgl_renderer", None) or self
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
        scl = tuple((xf or {}).get("scl", (1.0, 1.0, 1.0)))
        return (float(scl[0]), float(scl[1]), float(scl[2])), is_splat

    def _rot_shared_sync_q0_from_owner(self, owner: str) -> QtGui.QQuaternion:
        start_rot_deg, is_splat = self._get_owner_rot_deg(owner)
        self._rot_shared_start_rot = start_rot_deg
        self._rot_shared_is_splat = bool(is_splat)

        try:
            rx = float(start_rot_deg[0])
            ry = float(start_rot_deg[1])
            rz = float(start_rot_deg[2])
        except Exception:
            rx, ry, rz = 0.0, 0.0, 0.0

        q0 = self._rot_shared_q_from_euler_deg((rx, ry, rz))
        try:
            self._rot_owner_quat[owner] = q0
        except Exception:
            pass
        return q0

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
        # IMPORTANT:
        # gl_view builds its rotation matrices as R = Rz(-rz) @ Ry(-ry) @ Rx(-rx)
        # (you can see the sin signs in the matrices). So to match what the viewport
        # considers "rot_deg", we negate here when building the quaternion.
        try:
            rx = -float(rot_deg[0])
            ry = -float(rot_deg[1])
            rz = -float(rot_deg[2])
        except Exception:
            rx, ry, rz = 0.0, 0.0, 0.0

        qx = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(1.0, 0.0, 0.0), rx)
        qy = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(0.0, 1.0, 0.0), ry)
        qz = QtGui.QQuaternion.fromAxisAndAngle(QtGui.QVector3D(0.0, 0.0, 1.0), rz)
        q = (qz * qy * qx)
        try:
            if hasattr(q, "normalized"):
                q = q.normalized()
        except Exception:
            pass
        return q

    def _rot_shared_sync_q0_from_owner(self, owner: str) -> QtGui.QQuaternion:
        # Always sync drag-start quaternion from the current scene/outliner values.
        # This prevents snapping when the cached quat is stale.
        start_rot_deg, is_splat = self._get_owner_rot_deg(owner)
        self._rot_shared_start_rot = start_rot_deg
        self._rot_shared_is_splat = bool(is_splat)

        q0 = self._rot_shared_q_from_euler_deg(
            (float(start_rot_deg[0]), float(start_rot_deg[1]), float(start_rot_deg[2]))
        )

        try:
            self._rot_owner_quat[owner] = q0
        except Exception:
            pass

        return q0

    def _rot_shared_euler_deg_from_q(self, q: QtGui.QQuaternion):
        # quat -> euler in the standard Rz @ Ry @ Rx sense, then negate
        # to match gl_view's stored convention (since it applies -angles in matrices).
        w = float(q.scalar())
        xq = float(q.x())
        yq = float(q.y())
        zq = float(q.z())

        n = math.sqrt(w * w + xq * xq + yq * yq + zq * zq)
        if n > 1e-8:
            w /= n
            xq /= n
            yq /= n
            zq /= n

        r00 = 1.0 - 2.0 * (yq * yq + zq * zq)
        r10 = 2.0 * (xq * yq + zq * w)
        r20 = 2.0 * (xq * zq - yq * w)
        r21 = 2.0 * (yq * zq + xq * w)
        r22 = 1.0 - 2.0 * (xq * xq + yq * yq)

        sy = -r20
        sy = max(-1.0, min(1.0, sy))
        ry = math.asin(sy)
        cy = math.cos(ry)

        if abs(cy) > 1e-6:
            rx = math.atan2(r21, r22)
            rz = math.atan2(r10, r00)
        else:
            rx = 0.0
            r01 = 2.0 * (xq * yq - zq * w)
            r11 = 1.0 - 2.0 * (xq * xq + zq * zq)
            rz = math.atan2(-r01, r11)

        # negate to match gl_view's convention
        return (-math.degrees(rx), -math.degrees(ry), -math.degrees(rz))

    def _rot_shared_euler_deg_from_q_continuous(self, q: QtGui.QQuaternion, prev_rot_deg):
        # Get the "principal" euler triple from quat
        rx1, ry1, rz1 = self._rot_shared_euler_deg_from_q(q)

        px = float(prev_rot_deg[0])
        py = float(prev_rot_deg[1])
        pz = float(prev_rot_deg[2])

        def unwrap_to_prev(rx, ry, rz):
            rx = self._unwrap_deg(px, float(rx))
            ry = self._unwrap_deg(py, float(ry))
            rz = self._unwrap_deg(pz, float(rz))
            return rx, ry, rz

        def score(rx, ry, rz):
            rx, ry, rz = unwrap_to_prev(rx, ry, rz)
            dx = rx - px
            dy = ry - py
            dz = rz - pz
            return (dx * dx + dy * dy + dz * dz), (rx, ry, rz)

        # Second valid solution for Rz @ Ry @ Rx decomposition:
        # (rx + 180, 180 - ry, rz + 180) represents the same orientation.
        rx2 = float(rx1) + 180.0
        ry2 = 180.0 - float(ry1)
        rz2 = float(rz1) + 180.0

        s1, e1 = score(rx1, ry1, rz1)
        s2, e2 = score(rx2, ry2, rz2)

        return e1 if s1 <= s2 else e2

    def _unwrap_deg(self, prev_deg: float, new_deg_wrapped: float) -> float:
        # new_deg_wrapped is usually in [-180, 180]
        # return an equivalent angle close to prev_deg (continuous)
        d = new_deg_wrapped - prev_deg
        if d > 180.0:
            new_deg_wrapped -= 360.0
        elif d < -180.0:
            new_deg_wrapped += 360.0
        return new_deg_wrapped

    def _dbgprint(self, enabled: bool, *a, **k) -> None:
        if enabled:
            print(*a, **k)

    def _camdbg(self, *a) -> None:
        if bool(getattr(self, "_mgl_cam_debug", False)):
            print(*a, flush=True)

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
            cam_dist_origin = None
            cam_height = None
            cam_world = getattr(self, "_mgl_cam_world", None)
            if cam_world is not None:
                try:
                    cx = float(cam_world[0])
                    cy = float(cam_world[1])
                    cz = float(cam_world[2])
                    cam_height = cy
                    cam_dist_origin = math.sqrt((cx * cx) + (cy * cy) + (cz * cz))
                except Exception:
                    cam_dist_origin = None
                    cam_height = None
            if cam_dist_origin is None and Matrix44 is not None and np is not None:
                try:
                    zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
                    lookat = Matrix44.look_at(
                        (0.0, 0.0, zoom),
                        (0.0, 0.0, 0.0),
                        (0.0, 1.0, 0.0),
                    )
                    transform = Matrix44.identity(dtype="f4")
                    arc = getattr(self, "_mgl_arcball", None)
                    if arc is not None and hasattr(arc, "Transform"):
                        src = arc.Transform
                        if hasattr(src, "tolist"):
                            transform = Matrix44(src.tolist(), dtype="f4")
                        else:
                            transform = Matrix44(src, dtype="f4")
                    view = lookat * transform
                    view_np = np.array(view, dtype=np.float32)
                    inv = np.linalg.inv(view_np)
                    cam = inv @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                    cam_dist_origin = float(np.linalg.norm(cam[:3]))
                    cam_height = float(cam[1])
                except Exception:
                    cam_dist_origin = None
                    cam_height = None
            if cam_dist_origin is not None:
                lines.append(f"Cam->Origin: {cam_dist_origin:.2f}")
            if cam_height is not None:
                lines.append(f"Cam height: {cam_height:.2f}")
            fade_end_current = None
            try:
                fade_end_current = float(getattr(self, "_mgl_grid_fade_end_current", 0.0))
            except Exception:
                fade_end_current = None
            if fade_end_current is not None and fade_end_current > 0.0:
                lines.append(f"Grid fade radius: {fade_end_current:.2f}")
            else:
                try:
                    zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
                    if zoom > 0.0:
                        lines.append(f"Grid fade radius: {zoom * 2.0:.2f}")
                except Exception:
                    pass
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
        bg_key = self._mgl_uv_bg_path or ""
        if (
            self._mgl_uv_cache is None
            or not self._mgl_uv_cache_rect.isValid()
            or self._mgl_uv_cache_rect != panel
            or self._mgl_uv_cache_bg_key != bg_key
        ):
            self._mgl_uv_cache_rect = QtCore.QRectF(panel)
            self._mgl_uv_cache_bg_key = bg_key
            cache = QtGui.QPixmap(int(panel.width()), int(panel.height()))
            cache.fill(QtCore.Qt.transparent)
            uv_painter = QtGui.QPainter(cache)
            uv_painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            uv_painter.setPen(QtCore.Qt.NoPen)
            uv_painter.setBrush(QtGui.QColor(12, 14, 16, 235))
            uv_painter.drawRoundedRect(QtCore.QRectF(0, 0, panel.width(), panel.height()), 6, 6)
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

            if bg_key and os.path.exists(bg_key):
                bg = QtGui.QPixmap(bg_key)
                if not bg.isNull():
                    uv_painter.save()
                    uv_painter.setOpacity(0.9)
                    scaled = bg.scaled(
                        int(inner.width()),
                        int(inner.height()),
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation,
                    )
                    x0 = inner.left() + (inner.width() - scaled.width()) * 0.5
                    y0 = inner.top() + (inner.height() - scaled.height()) * 0.5
                    uv_painter.drawPixmap(int(x0), int(y0), scaled)
                    uv_painter.restore()

            if not self._mgl_uv_segments:
                uv_painter.setPen(QtGui.QColor("#e2e8f0"))
                uv_painter.drawText(QtCore.QRectF(0, 0, panel.width(), panel.height()), QtCore.Qt.AlignCenter, "No UVs")
            else:
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

    def _axis_ring_dir_world(
        self,
        cam: QtGui.QVector3D,
        ray_d: QtGui.QVector3D,
        center_w: QtGui.QVector3D,
        axis_world: QtGui.QVector3D,
    ) -> QtGui.QVector3D:
        # Matches the smoketest approach:
        # 1) closest point on the mouse ray to the gizmo center
        # 2) project onto plane perpendicular to the axis
        rd = QtGui.QVector3D(ray_d)
        if rd.lengthSquared() > 1e-12:
            rd.normalize()

        axis = QtGui.QVector3D(axis_world)
        if axis.lengthSquared() > 1e-12:
            axis.normalize()

        # Closest point on ray to center: cam + rd * dot(center-cam, rd)
        oc = center_w - cam
        t = float(QtGui.QVector3D.dotProduct(oc, rd))
        closest = cam + rd * t

        v = closest - center_w
        # Remove axis component so v lies in the ring plane
        v = v - axis * float(QtGui.QVector3D.dotProduct(v, axis))

        if v.lengthSquared() < 1e-12:
            # Fallback: any stable perpendicular vector
            up = QtGui.QVector3D(0.0, 1.0, 0.0)
            v = QtGui.QVector3D.crossProduct(axis, up)
            if v.lengthSquared() < 1e-12:
                up = QtGui.QVector3D(1.0, 0.0, 0.0)
                v = QtGui.QVector3D.crossProduct(axis, up)

        v.normalize()
        return v

    def _arcball_vec_world(
        self,
        mouse_px_dev: QtCore.QPointF,
        center_px_dev: QtCore.QPointF,
        radius_px: float,
        right_world: QtGui.QVector3D,
        up_world: QtGui.QVector3D,
        forward_world: QtGui.QVector3D,
    ) -> QtGui.QVector3D:
        # map 2D mouse to virtual sphere (arcball), then convert to world using camera basis
        dx = (float(mouse_px_dev.x()) - float(center_px_dev.x())) / max(1e-6, float(radius_px))
        # Match smoketest: flip Y so up is positive.
        dy = (float(center_px_dev.y()) - float(mouse_px_dev.y())) / max(1e-6, float(radius_px))

        # clamp to unit disk
        r2 = dx * dx + dy * dy
        if r2 > 1.0:
            invr = 1.0 / math.sqrt(r2)
            dx *= invr
            dy *= invr
            r2 = 1.0

        z = math.sqrt(max(0.0, 1.0 - r2))

        v = (
            right_world * float(dx)
            + up_world * float(dy)
            + forward_world * float(-z)
        )

        ln = float(v.length())
        if ln > 1e-8:
            v = v / ln
        return v

    def mousePressEvent(self, e):
        # Keep branch order stable so press behavior remains unchanged.
        if self._handle_mouse_press_moderngl(e):
            return
        if self._handle_mouse_press_example_pipeline(e):
            return
        if self._handle_mouse_press_legacy_left(e):
            return
        if self._handle_mouse_press_legacy_middle(e):
            return
        if self._handle_mouse_press_legacy_right(e):
            return
        super().mousePressEvent(e)

    def _handle_mouse_press_moderngl(self, e):
        # ModernGL path: gizmo interaction, scene picking, and Alt camera controls.
        if self._use_moderngl:
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
            if self._handle_mouse_press_moderngl_left_gizmo(e):
                return True
            if self._handle_mouse_press_moderngl_left_orbit_start(e, alt_pressed):
                return True
            if self._handle_mouse_press_moderngl_left_pick_start(e, alt_pressed):
                return True
            if self._handle_mouse_press_moderngl_middle_start(e, alt_pressed):
                return True
            if self._handle_mouse_press_moderngl_right_start(e, alt_pressed):
                return True
        return False

    def _handle_mouse_press_moderngl_left_gizmo(self, e):
        if e.button() != QtCore.Qt.LeftButton:
            return False
        try:
            ctx = self._handle_mouse_press_moderngl_left_gizmo_build_context(e)
            if ctx is None:
                return False
            return self._handle_mouse_press_moderngl_left_gizmo_dispatch(e=e, ctx=ctx)
        except Exception as exc:
            print("[GIZMO_PICK] failed:", exc, flush=True)
        return False

    def _handle_mouse_press_moderngl_left_gizmo_build_context(self, e):
        owner, pos = self._handle_mouse_press_moderngl_left_gizmo_owner_pos()
        if owner in (None, "") or pos is None or (np is None):
            return None

        mode = getattr(self, "_xform_gizmo_mode", "translate") or "translate"

        dpr, vw, vh = self._handle_mouse_press_moderngl_left_gizmo_pick_viewport()
        renderer = getattr(self, "_mgl_renderer", None) or self
        matrices = self._handle_mouse_press_moderngl_left_gizmo_pick_matrices(renderer=renderer)
        if matrices is None:
            return None
        P, V, M, PV = matrices

        g, axis_len, axes, p0 = self._handle_mouse_press_moderngl_left_gizmo_pick_basis(
            pos=pos,
            PV=PV,
            vw=vw,
            vh=vh,
        )

        px_dev, py_dev = self._handle_mouse_press_moderngl_left_gizmo_mouse_dev_pos(e=e, dpr=dpr)
        return {
            "owner": owner,
            "mode": mode,
            "dpr": dpr,
            "vw": vw,
            "vh": vh,
            "renderer": renderer,
            "P": P,
            "V": V,
            "M": M,
            "PV": PV,
            "g": g,
            "axis_len": axis_len,
            "axes": axes,
            "p0": p0,
            "px_dev": px_dev,
            "py_dev": py_dev,
        }

    def _handle_mouse_press_moderngl_left_gizmo_owner_pos(self):
        owner = getattr(self, "_xform_gizmo_owner", None)
        pos = getattr(self, "_xform_gizmo_pos", None)
        return owner, pos

    def _handle_mouse_press_moderngl_left_gizmo_pick_viewport(self):
        dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
        vw = float(self.width()) * dpr
        vh = float(self.height()) * dpr
        return dpr, vw, vh

    def _handle_mouse_press_moderngl_left_gizmo_pick_matrices(self, *, renderer):
        P = getattr(renderer, "_mgl_pick_proj", None)
        V = getattr(renderer, "_mgl_pick_view", None)
        M = getattr(renderer, "_mgl_pick_model", None)
        if P is None or V is None or M is None:
            return None
        PV = (P @ V @ M).astype("f4")
        return P, V, M, PV

    def _handle_mouse_press_moderngl_left_gizmo_pick_basis(self, *, pos, PV, vw, vh):
        g = np.array([float(pos[0]), float(pos[1]), float(pos[2])], dtype="f4")
        axis_len = 1.0
        axes = {
            "x": np.array([1.0, 0.0, 0.0], dtype="f4"),
            "y": np.array([0.0, 1.0, 0.0], dtype="f4"),
            "z": np.array([0.0, 0.0, 1.0], dtype="f4"),
        }
        p0 = self._handle_mouse_press_moderngl_left_gizmo_project_world(
            world_xyz=g,
            PV=PV,
            vw=vw,
            vh=vh,
        )
        return g, axis_len, axes, p0

    def _handle_mouse_press_moderngl_left_gizmo_dispatch(
        self,
        *,
        e,
        ctx,
    ):
        handled, _rot_shared, _hit = self._handle_mouse_press_moderngl_left_gizmo_dispatch_rotate(
            e=e,
            ctx=ctx,
        )
        if handled:
            return True
        return self._handle_mouse_press_moderngl_left_gizmo_dispatch_xform(e=e, ctx=ctx)

    def _handle_mouse_press_moderngl_left_gizmo_dispatch_rotate(
        self,
        *,
        e,
        ctx,
    ):
        # --- shared rotate gizmo drag start (NEW, RotateGizmoShared only) ---
        handled, rot_shared, hit = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick(
            e=e,
            mode=ctx["mode"],
            owner=ctx["owner"],
            g=ctx["g"],
            p0=ctx["p0"],
            dpr=ctx["dpr"],
            vw=ctx["vw"],
            vh=ctx["vh"],
            V=ctx["V"],
            M=ctx["M"],
            PV=ctx["PV"],
        )
        if handled:
            return True, rot_shared, hit

        # --- ROT_SHARED center-disc arcball drag (smoketest style) ---
        if self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick(
            e=e,
            mode=ctx["mode"],
            rot_shared=rot_shared,
            p0=ctx["p0"],
            hit=hit,
            owner=ctx["owner"],
            dpr=ctx["dpr"],
            px_dev=ctx["px_dev"],
            py_dev=ctx["py_dev"],
            V=ctx["V"],
            M=ctx["M"],
        ):
            return True, rot_shared, hit
        return False, rot_shared, hit

    def _handle_mouse_press_moderngl_left_gizmo_dispatch_xform(
        self,
        *,
        e,
        ctx,
    ):
        if self._handle_mouse_press_moderngl_left_gizmo_dispatch_xform_scale(e=e, ctx=ctx):
            return True
        return self._handle_mouse_press_moderngl_left_gizmo_dispatch_xform_translate(e=e, ctx=ctx)

    def _handle_mouse_press_moderngl_left_gizmo_dispatch_xform_scale(
        self,
        *,
        e,
        ctx,
    ):
        # --- scale gizmo drag start (axis cubes + center) ---
        scale_ctx = self._handle_mouse_press_moderngl_left_gizmo_scale_pick_context(
            p0=ctx["p0"],
            mode=ctx["mode"],
            owner=ctx["owner"],
            g=ctx["g"],
            axis_len=ctx["axis_len"],
            dpr=ctx["dpr"],
            vw=ctx["vw"],
            vh=ctx["vh"],
            P=ctx["P"],
            V=ctx["V"],
            M=ctx["M"],
            renderer=ctx["renderer"],
            px_dev=ctx["px_dev"],
            py_dev=ctx["py_dev"],
        )
        return self._handle_mouse_press_moderngl_left_gizmo_scale_pick(
            e=e,
            ctx=scale_ctx,
        )

    def _handle_mouse_press_moderngl_left_gizmo_scale_pick_context(
        self,
        *,
        p0,
        mode,
        owner,
        g,
        axis_len,
        dpr,
        vw,
        vh,
        P,
        V,
        M,
        renderer,
        px_dev,
        py_dev,
    ):
        return {
            "p0": p0,
            "mode": mode,
            "owner": owner,
            "g": g,
            "axis_len": axis_len,
            "dpr": dpr,
            "vw": vw,
            "vh": vh,
            "P": P,
            "V": V,
            "M": M,
            "renderer": renderer,
            "px_dev": px_dev,
            "py_dev": py_dev,
        }

    def _handle_mouse_press_moderngl_left_gizmo_dispatch_xform_translate(
        self,
        *,
        e,
        ctx,
    ):
        project = lambda world_xyz: self._handle_mouse_press_moderngl_left_gizmo_project_world(
            world_xyz=world_xyz,
            PV=ctx["PV"],
            vw=ctx["vw"],
            vh=ctx["vh"],
        )
        translate_ctx = self._handle_mouse_press_moderngl_left_gizmo_translate_pick_context(
            p0=ctx["p0"],
            mode=ctx["mode"],
            owner=ctx["owner"],
            g=ctx["g"],
            axes=ctx["axes"],
            axis_len=ctx["axis_len"],
            dpr=ctx["dpr"],
            vw=ctx["vw"],
            vh=ctx["vh"],
            P=ctx["P"],
            V=ctx["V"],
            M=ctx["M"],
            renderer=ctx["renderer"],
            px_dev=ctx["px_dev"],
            py_dev=ctx["py_dev"],
        )
        return self._handle_mouse_press_moderngl_left_gizmo_translate_pick(
            e=e,
            ctx=translate_ctx,
            project=project,
            dist_pt_seg=self._handle_mouse_press_moderngl_left_gizmo_dist_pt_seg,
        )

    def _handle_mouse_press_moderngl_left_gizmo_translate_pick_context(
        self,
        *,
        p0,
        mode,
        owner,
        g,
        axes,
        axis_len,
        dpr,
        vw,
        vh,
        P,
        V,
        M,
        renderer,
        px_dev,
        py_dev,
    ):
        return {
            "p0": p0,
            "mode": mode,
            "owner": owner,
            "g": g,
            "axes": axes,
            "axis_len": axis_len,
            "dpr": dpr,
            "vw": vw,
            "vh": vh,
            "P": P,
            "V": V,
            "M": M,
            "renderer": renderer,
            "px_dev": px_dev,
            "py_dev": py_dev,
        }

    def _handle_mouse_press_moderngl_left_gizmo_project_world(self, *, world_xyz, PV, vw, vh):
        return _gv_project_world(world_xyz=world_xyz, PV=PV, vw=vw, vh=vh)

    def _handle_mouse_press_moderngl_left_gizmo_mouse_dev_pos(self, *, e, dpr):
        # mouse in device pixels (must match project() output space)
        try:
            mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
        except Exception:
            mp = QtCore.QPointF(e.x(), e.y())
        return float(mp.x()) * float(dpr), float(mp.y()) * float(dpr)

    def _handle_mouse_press_moderngl_left_gizmo_dist_pt_seg(self, px2, py2, ax, ay, bx, by):
        return _gv_dist_pt_seg(px2=px2, py2=py2, ax=ax, ay=ay, bx=bx, by=by)

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick(
        self,
        *,
        e,
        mode,
        owner,
        g,
        p0,
        dpr,
        vw,
        vh,
        V,
        M,
        PV,
    ):
        if mode != "rotate":
            return False, None, None

        rot_shared, hit, mp = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_state(
            e=e,
            owner=owner,
        )
        if mp is None:
            return False, rot_shared, hit

        handled = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_dispatch(
            e=e,
            owner=owner,
            g=g,
            p0=p0,
            hit=hit,
            rot_shared=rot_shared,
            mp=mp,
            dpr=dpr,
            vw=vw,
            vh=vh,
            PV=PV,
            V=V,
            M=M,
        )
        return handled, rot_shared, hit

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_state(self, *, e, owner):
        rot_shared = getattr(self, "_rot_shared", None)
        center = getattr(self, "_rot_shared_center_px", None)
        mvp = getattr(self, "_rot_shared_mvp", None)
        if not self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_ready(
            rot_shared=rot_shared,
            center=center,
            mvp=mvp,
            owner=owner,
        ):
            return rot_shared, None, None

        mp, hit = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_hit(
            e=e,
            rot_shared=rot_shared,
            center=center,
            mvp=mvp,
        )
        self._mgl_log(
            f"[ROT_SHARED] pick hit={hit} mode={getattr(self,'_xform_gizmo_mode',None)} owner={owner}"
        )
        return rot_shared, hit, mp

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_dispatch(
        self,
        *,
        e,
        owner,
        g,
        p0,
        hit,
        rot_shared,
        mp,
        dpr,
        vw,
        vh,
        PV,
        V,
        M,
    ):
        if self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick(
            e=e,
            owner=owner,
            g=g,
            hit=hit,
            rot_shared=rot_shared,
            mp=mp,
            dpr=dpr,
            vw=vw,
            vh=vh,
            PV=PV,
        ):
            return True
        return self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_pick(
            e=e,
            owner=owner,
            g=g,
            p0=p0,
            hit=hit,
            rot_shared=rot_shared,
            V=V,
            M=M,
        )

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_ready(self, *, rot_shared, center, mvp, owner):
        return (
            rot_shared is not None
            and center is not None
            and mvp is not None
            and owner not in (None, "")
        )

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_hit(self, *, e, rot_shared, center, mvp):
        mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())

        # match smoketest-style picking (same args as hover/draw)
        band = max(12.0, float(rot_shared.xyz_ring_radius_px()) * 0.14)

        clip_enabled = bool(getattr(self, "_rot_clip_enabled", True))
        back_clip_cos = -math.cos(math.pi * float(getattr(self, "_rot_clip_frac", 0.30)))

        view_dir_local = getattr(self, "_rot_shared_view_dir_local", None)
        if view_dir_local is None:
            view_dir_local = QtGui.QVector3D(0.0, 0.0, 1.0)

        hit = rot_shared.pick_axis_2d(
            widget=self,
            center=center,
            mouse_px=QtCore.QPointF(mp),  # logical px
            viewport_w=self.width(),  # logical px (must match center/mouse)
            viewport_h=self.height(),
            mvp=mvp,
            view_dir_local=view_dir_local,
            back_clip_cos=float(back_clip_cos),
            clip_enabled=bool(clip_enabled),
            threshold_px=float(band),
        )
        return mp, hit

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick(
        self,
        *,
        e,
        owner,
        g,
        hit,
        rot_shared,
        mp,
        dpr,
        vw,
        vh,
        PV,
    ):
        if hit not in ("x", "y", "z"):
            return False

        self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_owner(owner=owner)
        q0, axis_world, center_w = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_vectors(
            owner=owner,
            hit=hit,
            g=g,
        )
        start_dir = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir(
            mp=mp,
            dpr=dpr,
            vw=vw,
            vh=vh,
            PV=PV,
            center_w=center_w,
            axis_world=axis_world,
        )
        if start_dir is None:
            return False

        start_rot_deg, _is_splat = self._get_owner_rot_deg(owner)
        self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_drag(
            rot_shared=rot_shared,
            hit=hit,
            q0=q0,
            axis_world=axis_world,
            start_dir=start_dir,
            start_rot_deg=start_rot_deg,
        )

        e.accept()
        return True

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_owner(self, *, owner):
        self._rot_shared_owner = owner
        self._begin_xform_history(owner)
        self._begin_xform_history(owner)

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_vectors(self, *, owner, hit, g):
        q0 = self._rot_shared_sync_q0_from_owner(owner)

        axis_local = (
            QtGui.QVector3D(1.0, 0.0, 0.0)
            if hit == "x"
            else (QtGui.QVector3D(0.0, 1.0, 0.0) if hit == "y" else QtGui.QVector3D(0.0, 0.0, 1.0))
        )
        axis_world = q0.rotatedVector(axis_local)
        if axis_world.length() > 1e-6:
            axis_world = axis_world / axis_world.length()
        else:
            axis_world = axis_local

        center_w = QtGui.QVector3D(float(g[0]), float(g[1]), float(g[2]))
        self._rot_shared_axis_center_world = center_w
        return q0, axis_world, center_w

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir(
        self,
        *,
        mp,
        dpr,
        vw,
        vh,
        PV,
        center_w,
        axis_world,
    ):
        # Cache invPV + viewport info for mouseMoveEvent (so move does not need P/V/M)
        try:
            invPV = np.linalg.inv(PV)
        except Exception as ex:
            print("[ROT_SHARED_AXIS_BEGIN_ERR] invPV", repr(ex), flush=True)
            return None

        self._rot_shared_invPV = invPV
        self._rot_shared_vw = float(vw)
        self._rot_shared_vh = float(vh)
        self._rot_shared_dpr = float(dpr)

        px = float(mp.x()) * dpr
        py = float(mp.y()) * dpr
        ray = _gv_ray_from_screen(invPV=invPV, px=px, py=py, vw=vw, vh=vh)
        if ray is None:
            print("[ROT_SHARED_AXIS_BEGIN_ERR] ray", flush=True)
            return None

        ray_o, ray_d = ray
        ro = QtGui.QVector3D(float(ray_o[0]), float(ray_o[1]), float(ray_o[2]))
        rd = QtGui.QVector3D(float(ray_d[0]), float(ray_d[1]), float(ray_d[2]))
        # Smoketest-style: closest point on ray to gizmo center, then project onto ring plane
        return self._axis_ring_dir_world(
            cam=ro,  # ro is fine as ray origin (it lies on the same ray)
            ray_d=rd,
            center_w=center_w,
            axis_world=axis_world,
        )

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_drag(
        self,
        *,
        rot_shared,
        hit,
        q0,
        axis_world,
        start_dir,
        start_rot_deg,
    ):
        # cache axis name + start euler for an axis-only update (prevents X/Z drift when dragging Y)
        self._rot_shared_axis = str(hit)
        self._rot_shared_axis_start_euler_deg = (
            float(start_rot_deg[0]),
            float(start_rot_deg[1]),
            float(start_rot_deg[2]),
        )
        self._rot_shared_axis_last_ang_deg = 0.0

        rot_shared.begin_axis_drag(
            axis=str(hit),
            start_rot=q0,
            axis_world=axis_world,
            start_dir=start_dir,
            start_euler_deg=(float(start_rot_deg[0]), float(start_rot_deg[1]), float(start_rot_deg[2])),
        )

        rot_shared.drag_axis.last_dir = QtGui.QVector3D(start_dir)

        # keep continuity so cur_dir can't flip 180 degrees mid-drag
        try:
            rot_shared.drag_axis.last_dir = QtGui.QVector3D(start_dir)
        except Exception:
            rot_shared.drag_axis.last_dir = start_dir

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_pick(
        self,
        *,
        e,
        owner,
        g,
        p0,
        hit,
        rot_shared,
        V,
        M,
    ):
        if hit != "view":
            return False

        self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_begin_owner(owner=owner)
        self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_sync_q0(owner=owner)
        self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_center(p0=p0)
        self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_forward(g=g, V=V, M=M)

        try:
            rot_shared.begin_view_ring_drag()  # smoketest-style: no ray math here
        except Exception:
            pass

        e.accept()
        return True

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_begin_owner(self, *, owner):
        self._rot_shared_owner = owner
        self._begin_xform_history(owner)

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_sync_q0(self, *, owner):
        # Persisted quaternion for this owner (same idea as axis rings).
        q0 = None
        try:
            q0 = self._rot_owner_quat.get(owner)
        except Exception:
            q0 = None

        if q0 is None:
            start_rot_deg, is_splat = self._get_owner_rot_deg(owner)
            self._rot_shared_start_rot = start_rot_deg
            self._rot_shared_is_splat = bool(is_splat)

            try:
                rx = float(start_rot_deg[0])
                ry = float(start_rot_deg[1])
                rz = float(start_rot_deg[2])
            except Exception:
                rx, ry, rz = 0.0, 0.0, 0.0

            q0 = self._rot_shared_q_from_euler_deg((rx, ry, rz))
            try:
                self._rot_owner_quat[owner] = q0
            except Exception:
                pass

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_center(self, *, p0):
        # Store view ring center in DEVICE pixels (project() returns device px).
        try:
            self._rot_shared_view_center_px = QtCore.QPointF(float(p0[0]), float(p0[1]))
        except Exception:
            self._rot_shared_view_center_px = None

    def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_forward(self, *, g, V, M):
        # Compute camera forward direction in world (same logic you had before).
        forward_world = QtGui.QVector3D(0.0, 0.0, -1.0)
        try:
            VM = (np.asarray(V, dtype=np.float32) @ np.asarray(M, dtype=np.float32)).astype(np.float32)
            invVM = np.linalg.inv(VM)
            cam4 = invVM @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
            cw = float(cam4[3]) if abs(float(cam4[3])) > 1e-8 else 1.0
            cam = cam4[:3] / cw

            f = g - cam
            ln = float(np.linalg.norm(f))
            if ln > 1e-6:
                f = f / ln
                forward_world = QtGui.QVector3D(float(f[0]), float(f[1]), float(f[2]))
        except Exception:
            pass

        self._rot_shared_view_forward_world = forward_world

    def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick(
        self,
        *,
        e,
        mode,
        rot_shared,
        p0,
        hit,
        owner,
        dpr,
        px_dev,
        py_dev,
        V,
        M,
    ):
        if mode == "rotate" and rot_shared is not None and p0 is not None and hit is None:
            try:
                prepared = self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_prepare(
                    owner=owner,
                    rot_shared=rot_shared,
                    p0=p0,
                    dpr=dpr,
                    px_dev=px_dev,
                    py_dev=py_dev,
                    V=V,
                    M=M,
                )
                if prepared is None:
                    return False

                self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_begin_state(
                    p0=p0,
                    px_dev=px_dev,
                    py_dev=py_dev,
                    **prepared,
                )

                e.accept()
                return True
            except Exception as ex:
                try:
                    self._mgl_log("[ROT_SHARED_ARC_BEGIN_ERR] " + repr(ex))
                except Exception:
                    print("[ROT_SHARED_ARC_BEGIN_ERR] " + repr(ex), flush=True)
        return False

    def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_prepare(
        self,
        *,
        owner,
        rot_shared,
        p0,
        dpr,
        px_dev,
        py_dev,
        V,
        M,
    ):
        disc_r = float(rot_shared.xyz_ring_radius_px()) * float(dpr)
        dx0 = float(px_dev) - float(p0[0])
        dy0 = float(py_dev) - float(p0[1])
        if (dx0 * dx0 + dy0 * dy0) > (disc_r * disc_r):
            return None

        self._rot_shared_owner = owner
        self._begin_xform_history(owner)

        # Always sync q0 from the owner's CURRENT outliner rotation
        # (prevents first-drag snap after manual edits / zeroing)
        q0 = self._rot_shared_sync_q0_from_owner(owner)
        right_world, up_world, forward_world = self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_basis(
            V=V,
            M=M,
        )
        return {
            "disc_r": disc_r,
            "q0": q0,
            "right_world": right_world,
            "up_world": up_world,
            "forward_world": forward_world,
        }

    def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_basis(self, *, V, M):
        # camera basis from inverse view*model (matches pick/unproject space)
        invVM = np.linalg.inv(
            (np.asarray(V, dtype=np.float32) @ np.asarray(M, dtype=np.float32)).astype(np.float32)
        )
        r = invVM[:3, 0]
        u = invVM[:3, 1]
        f = -invVM[:3, 2]  # camera looks down -Z

        right_world = QtGui.QVector3D(float(r[0]), float(r[1]), float(r[2]))
        up_world = QtGui.QVector3D(float(u[0]), float(u[1]), float(u[2]))
        forward_world = QtGui.QVector3D(float(f[0]), float(f[1]), float(f[2]))

        # normalize basis for stability
        try:
            if right_world.length() > 1e-6:
                right_world = right_world / right_world.length()
            if up_world.length() > 1e-6:
                up_world = up_world / up_world.length()
            if forward_world.length() > 1e-6:
                forward_world = forward_world / forward_world.length()
        except Exception:
            pass
        return right_world, up_world, forward_world

    def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_begin_state(
        self,
        *,
        p0,
        px_dev,
        py_dev,
        disc_r,
        q0,
        right_world,
        up_world,
        forward_world,
    ):
        # stash arcball drag state
        self._rot_shared_arc_active = True
        self._rot_shared_arc_center_px = QtCore.QPointF(float(p0[0]), float(p0[1]))
        self._rot_shared_arc_radius_px = float(disc_r)
        self._rot_shared_arc_right_world = right_world
        self._rot_shared_arc_up_world = up_world
        self._rot_shared_arc_forward_world = forward_world
        self._rot_shared_arc_start_q = q0

        mouse_pf = QtCore.QPointF(float(px_dev), float(py_dev))
        start_vec = self._arcball_vec_world(
            mouse_px_dev=mouse_pf,
            center_px_dev=self._rot_shared_arc_center_px,
            radius_px=self._rot_shared_arc_radius_px,
            right_world=right_world,
            up_world=up_world,
            forward_world=forward_world,
        )
        self._rot_shared_arc_start_vec = start_vec

    def _handle_mouse_press_moderngl_left_gizmo_axis_local(self, axis):
        if axis == "x":
            return np.array([1.0, 0.0, 0.0], dtype="f4")
        if axis == "y":
            return np.array([0.0, 1.0, 0.0], dtype="f4")
        if axis == "z":
            return np.array([0.0, 0.0, 1.0], dtype="f4")
        return None

    def _handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(self, *, owner):
        try:
            rot_deg, _is_splat = self._get_owner_rot_deg(owner)
            rx, ry, rz = float(rot_deg[0]), float(rot_deg[1]), float(rot_deg[2])
            cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
            cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
            cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))

            Rx = np.array(
                [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, cx, sx, 0.0],
                    [0.0, -sx, cx, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                dtype=np.float32,
            )
            Ry = np.array(
                [
                    [cy, 0.0, -sy, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [sy, 0.0, cy, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                dtype=np.float32,
            )
            Rz = np.array(
                [
                    [cz, sz, 0.0, 0.0],
                    [-sz, cz, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                dtype=np.float32,
            )
            return (Rz @ Ry @ Rx).astype(np.float32)
        except Exception:
            return np.eye(4, dtype=np.float32)

    def _handle_mouse_press_moderngl_left_gizmo_scale_pick(
        self,
        *,
        e,
        ctx,
    ):
        if ctx["p0"] is not None and ctx["mode"] == "scale":
            try:
                use_local, R, pick_axis, dx0, dy0 = self._handle_mouse_press_moderngl_left_gizmo_scale_prepare(
                    p0=ctx["p0"],
                    mode=ctx["mode"],
                    owner=ctx["owner"],
                    g=ctx["g"],
                    axis_len=ctx["axis_len"],
                    dpr=ctx["dpr"],
                    vw=ctx["vw"],
                    vh=ctx["vh"],
                    P=ctx["P"],
                    V=ctx["V"],
                    M=ctx["M"],
                    renderer=ctx["renderer"],
                    px_dev=ctx["px_dev"],
                    py_dev=ctx["py_dev"],
                )
                if self._handle_mouse_press_moderngl_left_gizmo_scale_start(
                    e=e,
                    ctx=ctx,
                    pick_axis=pick_axis,
                    dx0=dx0,
                    dy0=dy0,
                    use_local=use_local,
                    R=R,
                ):
                    return True
            except Exception as ex:
                print("[GIZMO_SCALE_PICK] failed:", ex, flush=True)
        return False

    def _handle_mouse_press_moderngl_left_gizmo_scale_prepare(
        self,
        *,
        p0,
        mode,
        owner,
        g,
        axis_len,
        dpr,
        vw,
        vh,
        P,
        V,
        M,
        renderer,
        px_dev,
        py_dev,
    ):
        use_local, R = self._handle_mouse_press_moderngl_left_gizmo_scale_basis(owner=owner)
        axis_proj = self._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj(
            g=g,
            axis_len=axis_len,
            dpr=dpr,
            vw=vw,
            vh=vh,
            P=P,
            V=V,
            M=M,
            R=R,
            renderer=renderer,
        )
        pick_axis, dx0, dy0 = self._handle_mouse_press_moderngl_left_gizmo_scale_pick_axis(
            p0=p0,
            axis_proj=axis_proj,
            px_dev=px_dev,
            py_dev=py_dev,
        )
        return use_local, R, pick_axis, dx0, dy0

    def _handle_mouse_press_moderngl_left_gizmo_scale_basis(self, *, owner):
        use_local = bool(getattr(self, "_xform_use_local", False))
        # Rotation matrix (scale gizmo follows object orientation).
        R = np.eye(4, dtype=np.float32)
        if use_local:
            R = self._handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(owner=owner)
        return use_local, R

    def _handle_mouse_press_moderngl_left_gizmo_scale_axis_proj(
        self,
        *,
        g,
        axis_len,
        dpr,
        vw,
        vh,
        P,
        V,
        M,
        R,
        renderer,
    ):
        T = np.eye(4, dtype=np.float32)
        T[0, 3] = float(g[0])
        T[1, 3] = float(g[1])
        T[2, 3] = float(g[2])

        # Match rotate gizmo scaling so cubes stay screen-sized.
        s = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale(
            dpr=dpr,
            P=P,
            V=V,
            M=M,
            T=T,
            R=R,
            renderer=renderer,
        )

        S = np.eye(4, dtype=np.float32)
        S[0, 0] = s
        S[1, 1] = s
        S[2, 2] = s

        TRS = (T @ R @ S).astype(np.float32)
        PVTRS = (P @ V @ M @ TRS).astype(np.float32)

        cube_axis_pos = axis_len - 0.18 + (0.12 * 0.5)
        return self._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_map(
            cube_axis_pos=cube_axis_pos,
            PVTRS=PVTRS,
            vw=vw,
            vh=vh,
        )

    def _handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_map(self, *, cube_axis_pos, PVTRS, vw, vh):
        return {
            "x": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
                local_xyz=(cube_axis_pos, 0.0, 0.0),
                PVTRS=PVTRS,
                vw=vw,
                vh=vh,
            ),
            "y": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
                local_xyz=(0.0, cube_axis_pos, 0.0),
                PVTRS=PVTRS,
                vw=vw,
                vh=vh,
            ),
            "z": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
                local_xyz=(0.0, 0.0, cube_axis_pos),
                PVTRS=PVTRS,
                vw=vw,
                vh=vh,
            ),
        }

    def _handle_mouse_press_moderngl_left_gizmo_scale_pick_axis(self, *, p0, axis_proj, px_dev, py_dev):
        # Avoid stealing orbit clicks far from the gizmo center.
        max_axis_len = 0.0
        for p1 in axis_proj.values():
            if p1 is None:
                continue
            dx1 = float(p1[0]) - float(p0[0])
            dy1 = float(p1[1]) - float(p0[1])
            dist = (dx1 * dx1 + dy1 * dy1) ** 0.5
            if dist > max_axis_len:
                max_axis_len = dist

        dx0 = float(px_dev) - float(p0[0])
        dy0 = float(py_dev) - float(p0[1])
        max_center = max(24.0, max_axis_len + 12.0)
        if (dx0 * dx0 + dy0 * dy0) > (max_center * max_center):
            axis_proj = {}

        pick_axis = None
        center_r = 16.0
        if (dx0 * dx0 + dy0 * dy0) <= (center_r * center_r):
            pick_axis = "u"
        else:
            best_axis = None
            best_d = 1e30
            for name, p1 in axis_proj.items():
                if p1 is None:
                    continue
                dx1 = float(px_dev) - float(p1[0])
                dy1 = float(py_dev) - float(p1[1])
                d = (dx1 * dx1 + dy1 * dy1) ** 0.5
                if d < best_d:
                    best_d = d
                    best_axis = name
            if best_axis is not None and best_d <= 16.0:
                pick_axis = best_axis
        return pick_axis, dx0, dy0

    def _handle_mouse_press_moderngl_left_gizmo_scale_start(
        self,
        *,
        e,
        ctx,
        pick_axis,
        dx0,
        dy0,
        use_local,
        R,
    ):
        if pick_axis is None:
            return False

        owner = self._handle_mouse_press_moderngl_left_gizmo_scale_start_begin(ctx=ctx, pick_axis=pick_axis)
        self._handle_mouse_press_moderngl_left_gizmo_scale_start_mode(
            pick_axis=pick_axis,
            p0=ctx["p0"],
            dx0=dx0,
            dy0=dy0,
            px_dev=ctx["px_dev"],
            use_local=use_local,
            R=R,
        )
        self._handle_mouse_press_moderngl_left_gizmo_scale_start_finalize(e=e, pick_axis=pick_axis, owner=owner)
        return True

    def _handle_mouse_press_moderngl_left_gizmo_scale_start_begin(self, *, ctx, pick_axis):
        renderer = ctx["renderer"]
        owner = ctx["owner"]
        is_splat = self._handle_mouse_press_moderngl_left_gizmo_owner_is_splat(
            renderer=renderer,
            owner=owner,
        )
        start_scl = self._handle_mouse_press_moderngl_left_gizmo_scale_start_scl(
            renderer=renderer,
            owner=owner,
            is_splat=is_splat,
        )
        self._handle_mouse_press_moderngl_left_gizmo_scale_begin_drag(
            owner=owner,
            g=ctx["g"],
            pick_axis=pick_axis,
            is_splat=is_splat,
            start_scl=start_scl,
        )
        return owner

    def _handle_mouse_press_moderngl_left_gizmo_scale_start_mode(
        self,
        *,
        pick_axis,
        p0,
        dx0,
        dy0,
        px_dev,
        use_local,
        R,
    ):
        if pick_axis == "u":
            self._handle_mouse_press_moderngl_left_gizmo_scale_start_uniform(
                p0=p0,
                dx0=dx0,
                dy0=dy0,
                px_dev=px_dev,
            )
            return
        self._handle_mouse_press_moderngl_left_gizmo_scale_start_axis(
            pick_axis=pick_axis,
            use_local=use_local,
            R=R,
        )

    def _handle_mouse_press_moderngl_left_gizmo_scale_start_finalize(self, *, e, pick_axis, owner):
        # Important: prevent old click-pick/orbit press state from interfering.
        self._mgl_pick_press_pos = None
        print("[GIZMO_SCALE_PICK] axis=", pick_axis, "owner=", owner, flush=True)
        self.setCursor(QtCore.Qt.SizeAllCursor)
        e.accept()

    def _handle_mouse_press_moderngl_left_gizmo_scale_start_scl(self, *, renderer, owner, is_splat):
        get_xf = (
            getattr(renderer, "_mgl_get_scene_splat_xform", None)
            if is_splat
            else getattr(renderer, "_mgl_get_scene_asset_xform", None)
        )
        xf = get_xf(owner) if callable(get_xf) else {}
        scl = tuple((xf or {}).get("scl", (1.0, 1.0, 1.0)))
        try:
            return (float(scl[0]), float(scl[1]), float(scl[2]))
        except Exception:
            return (1.0, 1.0, 1.0)

    def _handle_mouse_press_moderngl_left_gizmo_scale_begin_drag(self, *, owner, g, pick_axis, is_splat, start_scl):
        self._xform_dragging = True
        self._begin_xform_history(owner)
        self._xform_drag_mode = "scale"
        self._xform_drag_axis = pick_axis
        self._xform_drag_owner = owner
        self._xform_drag_kind = "splat" if is_splat else "mesh"
        self._xform_drag_start_pos = g.copy()
        self._xform_gizmo_pos_locked = True
        self._xform_drag_s0 = None
        self._xform_drag_start_scl = start_scl
        self._xform_scale_start_dist = None
        self._xform_scale_axis_world = None
        self._xform_scale_center_px = None
        self._xform_scale_start_px = None

    def _handle_mouse_press_moderngl_left_gizmo_scale_start_uniform(self, *, p0, dx0, dy0, px_dev):
        self._xform_scale_center_px = QtCore.QPointF(float(p0[0]), float(p0[1]))
        self._xform_scale_start_dist = max(1e-6, (dx0 * dx0 + dy0 * dy0) ** 0.5)
        try:
            self._xform_scale_start_px = float(px_dev)
        except Exception:
            self._xform_scale_start_px = None

    def _handle_mouse_press_moderngl_left_gizmo_scale_start_axis(self, *, pick_axis, use_local, R):
        axis_local = self._handle_mouse_press_moderngl_left_gizmo_axis_local(pick_axis)
        if axis_local is None:
            self._xform_scale_axis_world = None
            return
        axis_world = axis_local
        if use_local:
            try:
                axis_world = (R[:3, :3] @ axis_local).astype(np.float32)
            except Exception:
                axis_world = axis_local
        ln = float(np.linalg.norm(axis_world))
        if ln > 1e-8:
            axis_world = axis_world / ln
        self._xform_scale_axis_world = axis_world

    def _handle_mouse_press_moderngl_left_gizmo_translate_pick(
        self,
        *,
        e,
        ctx,
        project,
        dist_pt_seg,
    ):
        p0, axis_dirs, axis_proj = self._handle_mouse_press_moderngl_left_gizmo_translate_prepare(
            ctx=ctx,
            project=project,
        )
        return self._handle_mouse_press_moderngl_left_gizmo_translate_pick_start(
            e=e,
            ctx=ctx,
            p0=p0,
            axis_dirs=axis_dirs,
            axis_proj=axis_proj,
            dist_pt_seg=dist_pt_seg,
        )

    def _handle_mouse_press_moderngl_left_gizmo_translate_pick_start(
        self,
        *,
        e,
        ctx,
        p0,
        axis_dirs,
        axis_proj,
        dist_pt_seg,
    ):
        return bool(
            self._handle_mouse_press_moderngl_left_gizmo_translate_start(
                e=e,
                ctx=ctx,
                p0=p0,
                axis_dirs=axis_dirs,
                axis_proj=axis_proj,
                dist_pt_seg=dist_pt_seg,
            )
        )

    def _handle_mouse_press_moderngl_left_gizmo_translate_prepare(
        self,
        *,
        ctx,
        project,
    ):
        p0 = ctx["p0"]
        axis_dirs = ctx["axes"]
        axis_proj = {}
        if p0 is not None and ctx["mode"] == "translate":
            axis_dirs, R = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_dirs(
                owner=ctx["owner"],
                axes=ctx["axes"],
            )
            axis_proj, max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj(
                p0=p0,
                g=ctx["g"],
                axis_dirs=axis_dirs,
                axis_len=ctx["axis_len"],
                dpr=ctx["dpr"],
                vw=ctx["vw"],
                vh=ctx["vh"],
                P=ctx["P"],
                V=ctx["V"],
                M=ctx["M"],
                R=R,
                renderer=ctx["renderer"],
                project=project,
            )
            if self._handle_mouse_press_moderngl_left_gizmo_translate_is_far_from_center(
                p0=p0,
                px_dev=ctx["px_dev"],
                py_dev=ctx["py_dev"],
                max_axis_len=max_axis_len,
            ):
                p0 = None
        return p0, axis_dirs, axis_proj

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_dirs(self, *, owner, axes):
        axis_dirs = axes
        R = np.eye(4, dtype=np.float32)
        if bool(getattr(self, "_xform_use_local", False)):
            R = self._handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(owner=owner)
            try:
                axis_dirs = {
                    "x": (R[:3, :3] @ axes["x"]).astype("f4"),
                    "y": (R[:3, :3] @ axes["y"]).astype("f4"),
                    "z": (R[:3, :3] @ axes["z"]).astype("f4"),
                }
            except Exception:
                axis_dirs = axes
                R = np.eye(4, dtype=np.float32)
        return axis_dirs, R

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj(
        self,
        *,
        p0,
        g,
        axis_dirs,
        axis_len,
        dpr,
        vw,
        vh,
        P,
        V,
        M,
        R,
        renderer,
        project,
    ):
        axis_proj, max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled(
            p0=p0,
            g=g,
            axis_len=axis_len,
            dpr=dpr,
            vw=vw,
            vh=vh,
            P=P,
            V=V,
            M=M,
            R=R,
            renderer=renderer,
        )
        if not axis_proj:
            axis_proj, max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_fallback(
                p0=p0,
                g=g,
                axis_dirs=axis_dirs,
                axis_len=axis_len,
                project=project,
            )
        return axis_proj, max_axis_len

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled(
        self,
        *,
        p0,
        g,
        axis_len,
        dpr,
        vw,
        vh,
        P,
        V,
        M,
        R,
        renderer,
    ):
        # Project using the same scaled gizmo transform as the draw path.
        try:
            PVTRS = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_pvtrs(
                g=g,
                dpr=dpr,
                P=P,
                V=V,
                M=M,
                R=R,
                renderer=renderer,
            )
            axis_proj = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_map(
                axis_len=axis_len,
                PVTRS=PVTRS,
                vw=vw,
                vh=vh,
            )
            max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_axis_proj_max_len(p0=p0, axis_proj=axis_proj)
            return axis_proj, max_axis_len
        except Exception:
            return {}, 0.0

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_pvtrs(
        self,
        *,
        g,
        dpr,
        P,
        V,
        M,
        R,
        renderer,
    ):
        T = np.eye(4, dtype=np.float32)
        T[0, 3] = float(g[0])
        T[1, 3] = float(g[1])
        T[2, 3] = float(g[2])

        s = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale(
            dpr=dpr,
            P=P,
            V=V,
            M=M,
            T=T,
            R=R,
            renderer=renderer,
        )

        S = np.eye(4, dtype=np.float32)
        S[0, 0] = s
        S[1, 1] = s
        S[2, 2] = s

        TRS = (T @ R @ S).astype(np.float32)
        return (P @ V @ M @ TRS).astype(np.float32)

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_map(self, *, axis_len, PVTRS, vw, vh):
        line_end = float(axis_len) - 0.18
        return {
            "x": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
                local_xyz=(line_end, 0.0, 0.0),
                PVTRS=PVTRS,
                vw=vw,
                vh=vh,
            ),
            "y": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
                local_xyz=(0.0, line_end, 0.0),
                PVTRS=PVTRS,
                vw=vw,
                vh=vh,
            ),
            "z": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
                local_xyz=(0.0, 0.0, line_end),
                PVTRS=PVTRS,
                vw=vw,
                vh=vh,
            ),
        }

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale(self, *, dpr, P, V, M, T, R, renderer):
        vh_s = float(max(1, self.height())) * float(dpr)
        sm = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_multiplier(renderer=renderer)
        target_ring_px, ring_r = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_target()
        return _gv_gizmo_screen_scale(
            P=P,
            V=V,
            M=M,
            T=T,
            R=R,
            viewport_height_px=vh_s,
            scale_multiplier=sm,
            target_ring_px=target_ring_px,
            ring_radius=ring_r,
        )

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_multiplier(self, *, renderer):
        try:
            return float(getattr(renderer, "_mgl_scale_multiplier", 1.0))
        except Exception:
            return 1.0

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_target(self):
        rot_shared = getattr(self, "_rot_shared", None)
        if rot_shared is not None:
            return float(rot_shared.xyz_ring_radius_px()), float(getattr(rot_shared, "gizmo_radius", 0.9))
        return 110.0 * 1.3, 0.9

    def _handle_mouse_press_moderngl_left_gizmo_translate_project_local(self, *, local_xyz, PVTRS, vw, vh):
        return _gv_project_local(local_xyz=local_xyz, PVTRS=PVTRS, vw=vw, vh=vh)

    def _handle_mouse_press_moderngl_left_gizmo_axis_proj_max_len(self, *, p0, axis_proj):
        return _gv_axis_proj_max_len(p0=p0, axis_proj=axis_proj)

    def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_fallback(
        self,
        *,
        p0,
        g,
        axis_dirs,
        axis_len,
        project,
    ):
        axis_proj = {}
        max_axis_len = 0.0
        for name, a in axis_dirs.items():
            p1 = project(g + a * axis_len)
            if p1 is None:
                continue
            axis_proj[name] = p1
        max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_axis_proj_max_len(
            p0=p0,
            axis_proj=axis_proj,
        )
        return axis_proj, max_axis_len

    def _handle_mouse_press_moderngl_left_gizmo_translate_is_far_from_center(self, *, p0, px_dev, py_dev, max_axis_len):
        # Avoid stealing orbit clicks far from the gizmo center
        dx0 = float(px_dev) - float(p0[0])
        dy0 = float(py_dev) - float(p0[1])
        max_center = max(24.0, max_axis_len + 12.0)
        return (dx0 * dx0 + dy0 * dy0) > (max_center * max_center)

    def _handle_mouse_press_moderngl_left_gizmo_translate_start(
        self,
        *,
        e,
        ctx,
        p0,
        axis_dirs,
        axis_proj,
        dist_pt_seg,
    ):
        if p0 is None or ctx["mode"] != "translate":
            return False

        dx0, dy0 = self._handle_mouse_press_moderngl_left_gizmo_translate_start_offsets(
            p0=p0,
            px_dev=ctx["px_dev"],
            py_dev=ctx["py_dev"],
        )
        if self._handle_mouse_press_moderngl_left_gizmo_translate_start_try_view(
            e=e,
            owner=ctx["owner"],
            g=ctx["g"],
            dpr=ctx["dpr"],
            vw=ctx["vw"],
            vh=ctx["vh"],
            P=ctx["P"],
            V=ctx["V"],
            M=ctx["M"],
            renderer=ctx["renderer"],
            px_dev=ctx["px_dev"],
            py_dev=ctx["py_dev"],
            dx0=dx0,
            dy0=dy0,
        ):
            return True
        return self._handle_mouse_press_moderngl_left_gizmo_translate_start_try_axis(
            e=e,
            owner=ctx["owner"],
            g=ctx["g"],
            p0=p0,
            axis_dirs=axis_dirs,
            axis_proj=axis_proj,
            renderer=ctx["renderer"],
            px_dev=ctx["px_dev"],
            py_dev=ctx["py_dev"],
            dist_pt_seg=dist_pt_seg,
        )

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_offsets(self, *, p0, px_dev, py_dev):
        dx0 = float(px_dev) - float(p0[0])
        dy0 = float(py_dev) - float(p0[1])
        return dx0, dy0

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_try_view(
        self,
        *,
        e,
        owner,
        g,
        dpr,
        vw,
        vh,
        P,
        V,
        M,
        renderer,
        px_dev,
        py_dev,
        dx0,
        dy0,
    ):
        return self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_drag(
            e=e,
            owner=owner,
            g=g,
            dpr=dpr,
            vw=vw,
            vh=vh,
            P=P,
            V=V,
            M=M,
            renderer=renderer,
            px_dev=px_dev,
            py_dev=py_dev,
            dx0=dx0,
            dy0=dy0,
        )

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_try_axis(
        self,
        *,
        e,
        owner,
        g,
        p0,
        axis_dirs,
        axis_proj,
        renderer,
        px_dev,
        py_dev,
        dist_pt_seg,
    ):
        return self._handle_mouse_press_moderngl_left_gizmo_translate_start_axis_drag(
            e=e,
            owner=owner,
            g=g,
            p0=p0,
            axis_dirs=axis_dirs,
            axis_proj=axis_proj,
            renderer=renderer,
            px_dev=px_dev,
            py_dev=py_dev,
            dist_pt_seg=dist_pt_seg,
        )

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_drag(
        self,
        *,
        e,
        owner,
        g,
        dpr,
        vw,
        vh,
        P,
        V,
        M,
        renderer,
        px_dev,
        py_dev,
        dx0,
        dy0,
    ):
        if not self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_is_center_hit(
            dx0=dx0,
            dy0=dy0,
            dpr=dpr,
        ):
            return False

        self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_init_drag(
            owner=owner,
            g=g,
            renderer=renderer,
        )
        self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_set_plane_hit(
            g=g,
            P=P,
            V=V,
            M=M,
            px_dev=px_dev,
            py_dev=py_dev,
            vw=vw,
            vh=vh,
        )
        self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_finalize(e=e)
        return True

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_init_drag(self, *, owner, g, renderer):
        # free-move on view plane (camera-facing)
        is_splat = self._handle_mouse_press_moderngl_left_gizmo_owner_is_splat(
            renderer=renderer,
            owner=owner,
        )
        self._handle_mouse_press_moderngl_left_gizmo_translate_begin_drag(
            owner=owner,
            g=g,
            is_splat=is_splat,
            axis="view",
        )
        self._xform_drag_axis_world = None
        self._xform_drag_plane_normal = None
        self._xform_drag_plane_start = None

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_set_plane_hit(
        self,
        *,
        g,
        P,
        V,
        M,
        px_dev,
        py_dev,
        vw,
        vh,
    ):
        n = self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_normal(V=V, M=M)
        ray = self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_ray(
            px_dev=px_dev,
            py_dev=py_dev,
            vw=vw,
            vh=vh,
            P=P,
            V=V,
            M=M,
        )
        if n is not None and ray is not None:
            ray_o, ray_d = ray
            hit = self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_hit(
                g=g,
                plane_normal=n,
                ray_o=ray_o,
                ray_d=ray_d,
            )
            if hit is not None:
                self._xform_drag_plane_normal = n
                self._xform_drag_plane_start = hit

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_finalize(self, *, e):
        # Important: prevent old click-pick/orbit press state from interfering.
        self._mgl_pick_press_pos = None
        self.setCursor(QtCore.Qt.SizeAllCursor)
        e.accept()

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_is_center_hit(self, *, dx0, dy0, dpr):
        try:
            center_r = 14.0 * float(dpr)
        except Exception:
            center_r = 14.0
        return (dx0 * dx0 + dy0 * dy0) <= (center_r * center_r)

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_normal(self, *, V, M):
        return _gv_plane_normal_from_vm(V=V, M=M)

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_ray(self, *, px_dev, py_dev, vw, vh, P, V, M):
        return self._handle_mouse_move_moderngl_xform_ray(px=px_dev, py=py_dev, vw=vw, vh=vh, P=P, V=V, M=M)

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_hit(self, *, g, plane_normal, ray_o, ray_d):
        return _gv_plane_hit(point_on_plane=g, plane_normal=plane_normal, ray_o=ray_o, ray_d=ray_d)

    def _handle_mouse_press_moderngl_left_gizmo_translate_start_axis_drag(
        self,
        *,
        e,
        owner,
        g,
        p0,
        axis_dirs,
        axis_proj,
        renderer,
        px_dev,
        py_dev,
        dist_pt_seg,
    ):
        best_axis = None
        best_d = 1e30
        for name, p1 in axis_proj.items():
            d = dist_pt_seg(px_dev, py_dev, p0[0], p0[1], p1[0], p1[1])
            if d < best_d:
                best_d = d
                best_axis = name

        if best_axis is None or best_d > 20.0:
            return False

        # Determine kind directly from renderer state to avoid stale selection state
        is_splat = self._handle_mouse_press_moderngl_left_gizmo_owner_is_splat(
            renderer=renderer,
            owner=owner,
        )
        self._handle_mouse_press_moderngl_left_gizmo_translate_begin_drag(
            owner=owner,
            g=g,
            is_splat=is_splat,
            axis=best_axis,
        )
        self._xform_drag_axis_world = axis_dirs.get(best_axis) if isinstance(axis_dirs, dict) else None

        # Important: prevent old click-pick/orbit press state from interfering
        self._mgl_pick_press_pos = None

        print("[GIZMO_PICK] axis=", best_axis, "d=", best_d, "owner=", owner, flush=True)
        self.setCursor(QtCore.Qt.SizeAllCursor)
        e.accept()
        return True

    def _handle_mouse_press_moderngl_left_gizmo_translate_begin_drag(self, *, owner, g, is_splat, axis):
        self._xform_dragging = True
        self._begin_xform_history(owner)
        self._xform_drag_mode = "translate"
        self._xform_drag_axis = axis
        self._xform_drag_owner = owner
        self._xform_drag_kind = "splat" if is_splat else "mesh"
        self._xform_drag_start_pos = g.copy()
        self._xform_gizmo_pos_locked = True
        self._xform_drag_s0 = None

    def _handle_mouse_press_moderngl_left_gizmo_owner_is_splat(self, *, renderer, owner):
        is_splat = False
        try:
            splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
            if not isinstance(splat_map, dict) or not splat_map:
                splat_map = getattr(renderer, "_mgl_scene_splats", None)
            if isinstance(splat_map, dict) and owner in splat_map:
                is_splat = True
        except Exception:
            is_splat = False
        return is_splat

    def _handle_mouse_press_moderngl_left_orbit_start(self, e, alt_pressed):
        if e.button() == QtCore.Qt.LeftButton and alt_pressed and self._mgl_arcball is not None:
            if bool(getattr(self, "_mgl_orbit_locked", True)):
                self._sync_locked_orbit_from_arcball()
                self._mgl_orbit_dragging = True
                self._mgl_orbit_last_pos = e.pos()
            else:
                self._mgl_arcball.onClickLeftDown(e.x(), e.y())
            self._mgl_pick_press_pos = e.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept()
            return True
        return False

    def _handle_mouse_press_moderngl_left_pick_start(self, e, alt_pressed):
        if e.button() == QtCore.Qt.LeftButton and not alt_pressed:
            # allow click-pick without orbiting
            self._mgl_pick_press_pos = e.pos()
            self.setCursor(QtCore.Qt.ArrowCursor)
            e.accept()
            return True
        return False

    def _handle_mouse_press_moderngl_middle_start(self, e, alt_pressed):
        if e.button() == QtCore.Qt.MiddleButton:
            if not alt_pressed:
                e.ignore()
                return True
            self._mgl_prev_x = e.x()
            self._mgl_prev_y = e.y()
            self.setCursor(QtCore.Qt.OpenHandCursor)
            e.accept()
            return True
        return False

    def _handle_mouse_press_moderngl_right_start(self, e, alt_pressed):
        if e.button() != QtCore.Qt.RightButton:
            return False
        if not alt_pressed:
            return self._handle_mouse_press_moderngl_right_start_fly(e)
        return self._handle_mouse_press_moderngl_right_start_zoom(e)

    def _handle_mouse_press_moderngl_right_start_fly(self, e):
        if not bool(getattr(self, "_fly_mode_enabled", False)):
            e.ignore()
            return True
        try:
            self._fps_nav_active = True
            self._fps_nav_last_t = time.perf_counter()
            self._fps_nav_look_last_pos = e.pos()
            try:
                if hasattr(e, "globalPosition"):
                    anchor = e.globalPosition().toPoint()
                elif hasattr(e, "globalPos"):
                    anchor = e.globalPos()
                else:
                    anchor = QtGui.QCursor.pos()
            except Exception:
                anchor = QtGui.QCursor.pos()
            self._fps_nav_cursor_anchor = anchor
            self._fps_nav_warping = False
        except Exception:
            pass
        try:
            self._fps_camera_active = True
            orbit_enabled = bool(getattr(self, "_orbit_cam_enabled", True))
            if orbit_enabled or getattr(self, "_fps_camera", None) is None:
                self._fps_cam_sync_from_orbit()
        except Exception:
            pass
        try:
            self._mgl_zoom_press_pos = None
        except Exception:
            pass
        try:
            self.setFocus(QtCore.Qt.MouseFocusReason)
        except Exception:
            pass
        try:
            if bool(getattr(self, "_fly_mode_enabled", False)):
                self.setCursor(QtCore.Qt.BlankCursor)
            else:
                self.setCursor(QtCore.Qt.ArrowCursor)
        except Exception:
            pass
        e.accept()
        return True

    def _handle_mouse_press_moderngl_right_start_zoom(self, e):
        self._mgl_zoom_press_pos = e.pos()
        self._mgl_zoom_start = float(self._mgl_camera_zoom)
        self._handle_mouse_press_moderngl_right_start_zoom_center()
        self._handle_mouse_press_moderngl_right_start_zoom_ray(e)
        self._handle_mouse_press_moderngl_right_start_zoom_cam_start()
        self._handle_mouse_press_moderngl_right_start_zoom_cam_dir()
        self.setCursor(QtCore.Qt.SizeVerCursor)
        e.accept()
        return True

    def _handle_mouse_press_moderngl_right_start_zoom_center(self):
        try:
            center = getattr(self, "_mgl_center", None)
            if center is None:
                center = (0.0, 0.0, 0.0)
            if np is not None:
                self._mgl_zoom_center_start = np.array(center, dtype=np.float32)
            else:
                self._mgl_zoom_center_start = (
                    float(center[0]),
                    float(center[1]),
                    float(center[2]),
                )
        except Exception:
            self._mgl_zoom_center_start = None

    def _handle_mouse_press_moderngl_right_start_zoom_ray(self, e):
        try:
            ray = self._ray_from_mouse(e.pos())
            if ray is not None:
                _, ray_d = ray
                if np is not None:
                    self._mgl_zoom_ray_dir = np.array(ray_d, dtype=np.float32)
                else:
                    self._mgl_zoom_ray_dir = (
                        float(ray_d[0]),
                        float(ray_d[1]),
                        float(ray_d[2]),
                    )
            else:
                self._mgl_zoom_ray_dir = None
        except Exception:
            self._mgl_zoom_ray_dir = None

    def _handle_mouse_press_moderngl_right_start_zoom_cam_start(self):
        try:
            cam_world = getattr(self, "_mgl_cam_world", None)
            if cam_world is None and np is not None:
                arc = getattr(self, "_mgl_arcball", None)
                zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
                if arc is not None and hasattr(arc, "Transform"):
                    rot = np.array(arc.Transform[:3, :3], dtype=np.float32)
                    scale = float(np.linalg.norm(rot, ord="fro") / math.sqrt(3.0))
                    if scale > 1e-6:
                        rot = rot / scale
                    cam_local = np.array([0.0, 0.0, zoom], dtype=np.float32)
                    cam_rot = rot.T @ cam_local
                    c0 = getattr(self, "_mgl_zoom_center_start", None)
                    if c0 is not None:
                        cam_world = cam_rot + np.array(c0, dtype=np.float32)
                    else:
                        cam_world = cam_rot
            if cam_world is not None:
                if np is not None:
                    self._mgl_zoom_cam_start = np.array(cam_world, dtype=np.float32)
                else:
                    self._mgl_zoom_cam_start = (
                        float(cam_world[0]),
                        float(cam_world[1]),
                        float(cam_world[2]),
                    )
            else:
                self._mgl_zoom_cam_start = None
        except Exception:
            self._mgl_zoom_cam_start = None

    def _handle_mouse_press_moderngl_right_start_zoom_cam_dir(self):
        try:
            cam_start = self._mgl_zoom_cam_start
            c0 = self._mgl_zoom_center_start
            if cam_start is not None and c0 is not None:
                if np is not None:
                    cs = np.array(cam_start, dtype=np.float32)
                    c = np.array(c0, dtype=np.float32)
                    v = cs - c
                    vn = float(np.linalg.norm(v))
                    if vn > 1e-6:
                        self._mgl_zoom_cam_dir = (v / vn).astype("f4")
                    else:
                        self._mgl_zoom_cam_dir = None
                else:
                    dx = float(cam_start[0]) - float(c0[0])
                    dy = float(cam_start[1]) - float(c0[1])
                    dz = float(cam_start[2]) - float(c0[2])
                    dn = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if dn > 1e-6:
                        self._mgl_zoom_cam_dir = (dx / dn, dy / dn, dz / dn)
                    else:
                        self._mgl_zoom_cam_dir = None
            else:
                self._mgl_zoom_cam_dir = None
        except Exception:
            self._mgl_zoom_cam_dir = None

    def _handle_mouse_press_example_pipeline(self, e):
        if self._use_example_pipeline:
            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False

            if e.button() == QtCore.Qt.LeftButton:
                if not alt_pressed:
                    e.ignore()
                    return True
                self._orbit_dragging = True
                self._orbit_last_pos = e.pos()
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                e.accept()
                return True

            if e.button() == QtCore.Qt.MiddleButton:
                if not alt_pressed:
                    e.ignore()
                    return True
                self._pan_dragging = True
                self._pan_last_pos = e.pos()
                self.setCursor(QtCore.Qt.OpenHandCursor)
                e.accept()
                return True

            if e.button() == QtCore.Qt.RightButton:
                if not alt_pressed:
                    e.ignore()
                    return True
                self._dolly_dragging = True
                self._dolly_press_pos = e.pos()
                self.setCursor(QtCore.Qt.SizeVerCursor)
                e.accept()
                return True
        return False

    def _handle_mouse_press_legacy_left(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False
            if not alt_pressed:
                e.ignore()
                return True
            self._orbit_dragging = True
            self._orbit_last_pos = e.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept()
            return True
        return False

    def _handle_mouse_press_legacy_middle(self, e):
        if e.button() == QtCore.Qt.MiddleButton:
            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False
            if not alt_pressed:
                e.ignore()
                return True
            self._pan_dragging = True
            self._pan_last_pos = e.pos()
            self.setCursor(QtCore.Qt.OpenHandCursor)
            e.accept()
            return True
        return False

    def _handle_mouse_press_legacy_right(self, e):
        if e.button() == QtCore.Qt.RightButton:
            alt_pressed = False
            try:
                alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
            except Exception:
                alt_pressed = False
            if not alt_pressed:
                e.ignore()
                return True
            self._dolly_dragging = True
            self._dolly_press_pos = e.pos()
            self._dolly_start_dist = float(self._cam_dist)
            self.setCursor(QtCore.Qt.SizeVerCursor)
            e.accept()
            return True
        return False

    def mouseMoveEvent(self, e):
        def _rot_dbg(msg: str) -> None:
            try:
                fn = getattr(self, "_mgl_log", None)
                if callable(fn):
                    fn(msg)
                else:
                    print(msg, flush=True)
            except Exception:
                try:
                    print(msg, flush=True)
                except Exception:
                    pass
        if self._handle_mouse_move_moderngl(e, _rot_dbg):
            return
        if self._handle_mouse_move_example_pipeline(e):
            return
        if self._handle_mouse_move_legacy_orbit(e):
            return
        if self._handle_mouse_move_legacy_pan(e):
            return
        if self._handle_mouse_move_legacy_dolly(e):
            return
        super().mouseMoveEvent(e)

    def _handle_mouse_move_moderngl(self, e, _rot_dbg):
        if self._use_moderngl:
            # cache mouse pos for ROT_SHARED hover (logical pixels, matches project_to_screen usage)
            try:
                mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
                self._rot_shared_mouse_px = QtCore.QPointF(mp)
                self._xform_mouse_px = QtCore.QPointF(mp)
            except Exception:
                mp = None

            if self._handle_mouse_move_moderngl_fps_nav(e):
                return True

            self._log_mouse_move_rot_shared_state(_rot_dbg)
            self._update_mouse_move_xform_hover()

            if self._handle_mouse_move_moderngl_rot_shared_view_drag(e, _rot_dbg):
                return True

            if self._handle_mouse_move_moderngl_rot_shared_arc_drag(e):
                return True

            if self._handle_mouse_move_moderngl_rot_shared_axis_drag(e, mp, _rot_dbg):
                return True


            if self._handle_mouse_move_moderngl_xform_drag(e):
                return True

            if self._handle_mouse_move_moderngl_arcball_drag(e):
                return True

            if self._handle_mouse_move_moderngl_pan_drag(e):
                return True

            if self._handle_mouse_move_moderngl_zoom_drag(e):
                return True
        return False

    def _handle_mouse_move_moderngl_fps_nav(self, e):
        if bool(getattr(self, "_fps_nav_active", False)) and (e.buttons() & QtCore.Qt.RightButton):
            try:
                if bool(getattr(self, "_fly_mode_enabled", False)):
                    if bool(getattr(self, "_fps_nav_warping", False)):
                        self._fps_nav_warping = False
                    anchor = getattr(self, "_fps_nav_cursor_anchor", None)
                    if anchor is None:
                        try:
                            anchor = QtGui.QCursor.pos()
                        except Exception:
                            anchor = None
                        self._fps_nav_cursor_anchor = anchor
                    try:
                        if hasattr(e, "globalPosition"):
                            gpos = e.globalPosition().toPoint()
                        elif hasattr(e, "globalPos"):
                            gpos = e.globalPos()
                        else:
                            gpos = QtGui.QCursor.pos()
                    except Exception:
                        gpos = QtGui.QCursor.pos()
                    if anchor is not None and gpos is not None:
                        dx = float(gpos.x() - anchor.x())
                        dy = float(gpos.y() - anchor.y())
                        if abs(dx) > 0.0 or abs(dy) > 0.0:
                            self._fps_apply_look(dx, dy)
                            try:
                                self._fps_nav_warping = True
                                QtGui.QCursor.setPos(anchor)
                            except Exception:
                                pass
                else:
                    last = getattr(self, "_fps_nav_look_last_pos", None)
                    if last is None:
                        last = e.pos()
                    dx = float(e.pos().x() - last.x())
                    dy = float(e.pos().y() - last.y())
                    self._fps_nav_look_last_pos = e.pos()
                    self._fps_apply_look(dx, dy)
            except Exception:
                pass
            try:
                self.update()
            except Exception:
                pass
            e.accept()
            return True
        return False

    def _log_mouse_move_rot_shared_state(self, _rot_dbg):
        # light state dump, throttled
        try:
            if bool(getattr(self, "_mgl_splat_log", False)):
                now = time.perf_counter()
                last = float(getattr(self, "_rot_shared_dbg_t", 0.0))
                if (now - last) > 0.20:
                    self._rot_shared_dbg_t = now
                    rot_shared = getattr(self, "_rot_shared", None)
                    drag_axis = getattr(rot_shared, "drag_axis", None) if rot_shared is not None else None
                    _rot_dbg(
                        "[ROT_DBG]"
                        f" mode={getattr(self,'_xform_gizmo_mode',None)}"
                        f" _rot_shared_dragging={bool(getattr(self,'_rot_shared_dragging',False))}"
                        f" _rot_shared_axis={getattr(self,'_rot_shared_axis',None)}"
                        f" drag_axis.active={bool(getattr(drag_axis,'active',False))}"
                        f" drag_axis.axis={getattr(drag_axis,'axis',None)}"
                    )
        except Exception:
            pass

    def _update_mouse_move_xform_hover(self):
        # Hover highlight needs repaints even when not dragging
        if (
            getattr(self, "_xform_gizmo_mode", "") == "rotate"
            and getattr(self, "_rot_shared", None) is not None
            and not getattr(self, "_rot_shared_dragging", False)
        ):
            self.update()
        if (
            getattr(self, "_xform_gizmo_mode", "") in ("translate", "scale")
            and not getattr(self, "_xform_dragging", False)
        ):
            self.update()

    def _handle_mouse_move_moderngl_rot_shared_view_drag(self, e, _rot_dbg):
        # --- ROT_SHARED view-ring drag update (smoketest style) ---
        rot_shared = getattr(self, "_rot_shared", None)
        if (
            rot_shared is not None
            and bool(getattr(rot_shared, "drag_view", False))
            and (e.buttons() & QtCore.Qt.LeftButton)
        ):
            try:
                prepared = self._handle_mouse_move_moderngl_rot_shared_view_drag_prepare(
                    e=e,
                    rot_shared=rot_shared,
                )
                if prepared is None:
                    return True
                owner, qnew = prepared
                self._handle_mouse_move_moderngl_rot_shared_view_drag_apply(
                    e=e,
                    owner=owner,
                    qnew=qnew,
                )
                return True

            except Exception as ex:
                _rot_dbg("[ROT_SHARED_VIEW_MOVE_ERR] " + repr(ex))
        return False

    def _handle_mouse_move_moderngl_rot_shared_view_drag_prepare(self, *, e, rot_shared):
        owner = getattr(self, "_rot_shared_owner", None)
        if owner is None:
            return None

        center_pf = getattr(self, "_rot_shared_view_center_px", None)
        if not isinstance(center_pf, QtCore.QPointF):
            return None

        forward_world = getattr(self, "_rot_shared_view_forward_world", None)
        if not isinstance(forward_world, QtGui.QVector3D):
            return None

        dpr = float(self.devicePixelRatioF()) if hasattr(self, "devicePixelRatioF") else 1.0
        mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
        mouse_pf = QtCore.QPointF(float(mp.x()) * dpr, float(mp.y()) * dpr)

        qcur = self._handle_mouse_move_moderngl_rot_shared_view_drag_qcur(owner=owner)
        qnew = rot_shared.update_view_ring_drag(mouse_pf, center_pf, forward_world, qcur)
        if qnew is None:
            return None

        try:
            if hasattr(qnew, "normalized"):
                qnew = qnew.normalized()
        except Exception:
            pass
        return owner, qnew

    def _handle_mouse_move_moderngl_rot_shared_view_drag_qcur(self, *, owner):
        qcur = None
        try:
            qcur = self._rot_owner_quat.get(owner)
        except Exception:
            qcur = None

        if qcur is None:
            rot_deg, is_splat = self._get_owner_rot_deg(owner)
            self._rot_shared_is_splat = bool(is_splat)
            qcur = self._rot_shared_q_from_euler_deg(
                (float(rot_deg[0]), float(rot_deg[1]), float(rot_deg[2]))
            )
            try:
                self._rot_owner_quat[owner] = qcur
            except Exception:
                pass
        return qcur

    def _handle_mouse_move_moderngl_rot_shared_view_drag_apply(self, *, e, owner, qnew):
        try:
            self._rot_owner_quat[owner] = qnew
        except Exception:
            pass

        rx, ry, rz = self._rot_shared_euler_deg_from_q(qnew)

        cur_rot_deg, _is_splat = self._get_owner_rot_deg(owner)
        rx, ry, rz = self._rot_shared_euler_deg_from_q_continuous(qnew, cur_rot_deg)

        self._set_owner_rot_deg(
            owner,
            (rx, ry, rz),
            bool(getattr(self, "_rot_shared_is_splat", False)),
        )

        self.update()
        e.accept()

    def _handle_mouse_move_moderngl_rot_shared_arc_drag(self, e):
        if bool(getattr(self, "_rot_shared_arc_active", False)) and (e.buttons() & QtCore.Qt.LeftButton):
            try:
                prepared = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare(e=e)
                if prepared is None:
                    return True
                owner, qnew = prepared
                self._handle_mouse_move_moderngl_rot_shared_arc_drag_apply(
                    e=e,
                    owner=owner,
                    qnew=qnew,
                )
                return True

            except Exception as ex:
                try:
                    self._mgl_log("[ROT_SHARED_ARC_MOVE_ERR] " + repr(ex))
                except Exception:
                    pass
        return False

    def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare(self, *, e):
        owner = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_owner()
        if owner is None:
            return None

        mouse_ctx = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_mouse(e=e)
        if mouse_ctx is None:
            return None
        center_pf, mouse_pf = mouse_ctx

        basis = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_basis()
        if basis is None:
            return None
        start_vec, right_world, up_world, forward_world = basis

        qnew = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_qnew(
            start_vec=start_vec,
            center_pf=center_pf,
            mouse_pf=mouse_pf,
            right_world=right_world,
            up_world=up_world,
            forward_world=forward_world,
        )
        if qnew is None:
            return None
        return owner, qnew

    def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_owner(self):
        return getattr(self, "_rot_shared_owner", None)

    def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_mouse(self, *, e):
        center_pf = getattr(self, "_rot_shared_arc_center_px", None)
        if not isinstance(center_pf, QtCore.QPointF):
            return None
        dpr = float(self.devicePixelRatioF()) if hasattr(self, "devicePixelRatioF") else 1.0
        mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
        mouse_pf = QtCore.QPointF(float(mp.x()) * dpr, float(mp.y()) * dpr)
        return center_pf, mouse_pf

    def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_basis(self):
        start_vec = getattr(self, "_rot_shared_arc_start_vec", None)
        right_world = getattr(self, "_rot_shared_arc_right_world", None)
        up_world = getattr(self, "_rot_shared_arc_up_world", None)
        forward_world = getattr(self, "_rot_shared_arc_forward_world", None)
        if not isinstance(start_vec, QtGui.QVector3D):
            return None
        if not (
            isinstance(right_world, QtGui.QVector3D)
            and isinstance(up_world, QtGui.QVector3D)
            and isinstance(forward_world, QtGui.QVector3D)
        ):
            return None
        return start_vec, right_world, up_world, forward_world

    def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_qnew(
        self,
        *,
        start_vec,
        center_pf,
        mouse_pf,
        right_world,
        up_world,
        forward_world,
    ):
        radius_px = float(getattr(self, "_rot_shared_arc_radius_px", 1.0))
        cur_vec = self._arcball_vec_world(
            mouse_px_dev=mouse_pf,
            center_px_dev=center_pf,
            radius_px=radius_px,
            right_world=right_world,
            up_world=up_world,
            forward_world=forward_world,
        )

        q_delta = quat_from_two_vectors(start_vec, cur_vec)
        q0 = getattr(self, "_rot_shared_arc_start_q", None)
        if q0 is None:
            return None
        qnew = q_delta * q0
        try:
            if hasattr(qnew, "normalized"):
                qnew = qnew.normalized()
        except Exception:
            pass
        return qnew

    def _handle_mouse_move_moderngl_rot_shared_arc_drag_apply(self, *, e, owner, qnew):
        # persist quaternion cache (arcball uses qnew directly)
        try:
            self._rot_owner_quat[owner] = qnew
        except Exception:
            pass

        # APPLY to owner so the object visibly rotates during arcball drag
        # unwrap vs current outliner values so angles keep accumulating past 180
        try:
            rx, ry, rz = self._rot_shared_euler_deg_from_q(qnew)

            cur_rot_deg, _is_splat = self._get_owner_rot_deg(owner)
            rx = self._unwrap_deg(float(cur_rot_deg[0]), float(rx))
            ry = self._unwrap_deg(float(cur_rot_deg[1]), float(ry))
            rz = self._unwrap_deg(float(cur_rot_deg[2]), float(rz))

            self._set_owner_rot_deg(
                owner,
                (rx, ry, rz),
                bool(getattr(self, "_rot_shared_is_splat", False)),
            )
        except Exception:
            pass

        self.update()
        e.accept()

    def _handle_mouse_move_moderngl_rot_shared_axis_drag(self, e, mp, _rot_dbg):
        rot_shared = getattr(self, "_rot_shared", None)
        if not (
            rot_shared is not None
            and getattr(rot_shared, "drag_axis", None) is not None
            and bool(getattr(rot_shared.drag_axis, "active", False))
            and (e.buttons() & QtCore.Qt.LeftButton)
        ):
            return False
        try:
            prepared = self._handle_mouse_move_moderngl_rot_shared_axis_prepare(
                e=e,
                mp=mp,
                rot_shared=rot_shared,
                _rot_dbg=_rot_dbg,
            )
            if prepared is None:
                return True
            owner, qnew = prepared
            self._handle_mouse_move_moderngl_rot_shared_axis_apply(
                owner=owner,
                rot_shared=rot_shared,
                qnew=qnew,
                _rot_dbg=_rot_dbg,
            )

        except Exception as ex:
            _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] " + repr(ex))
            return False
        return False

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare(self, e, mp, rot_shared, _rot_dbg):
        if np is None:
            _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] np is None")
            return None

        owner = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_owner(_rot_dbg=_rot_dbg)
        if not owner:
            return None

        mp = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_mouse(e=e, mp=mp)

        ray = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ray(
            mp=mp,
            _rot_dbg=_rot_dbg,
        )
        if ray is None:
            return None
        cam, ray_d = ray

        ring_dir = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ring_dir(
            rot_shared=rot_shared,
            cam=cam,
            ray_d=ray_d,
            _rot_dbg=_rot_dbg,
        )
        if ring_dir is None:
            return None
        cur_dir, axis_world = ring_dir

        self._handle_mouse_move_moderngl_rot_shared_axis_prepare_log(
            rot_shared=rot_shared,
            owner=owner,
            axis_world=axis_world,
            ray_d=ray_d,
            cur_dir=cur_dir,
            _rot_dbg=_rot_dbg,
        )

        qnew = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_qnew(
            rot_shared=rot_shared,
            cur_dir=cur_dir,
            _rot_dbg=_rot_dbg,
        )
        if qnew is None:
            return None
        return owner, qnew

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_owner(self, *, _rot_dbg):
        owner = getattr(self, "_rot_shared_owner", None)
        if not owner:
            _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] missing _rot_shared_owner")
            return None
        return owner

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_mouse(self, *, e, mp):
        if mp is None:
            return e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
        return mp

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_log(
        self,
        *,
        rot_shared,
        owner,
        axis_world,
        ray_d,
        cur_dir,
        _rot_dbg,
    ):
        den_dbg = float(QtGui.QVector3D.dotProduct(axis_world, ray_d))
        _rot_dbg(
            "[ROT_SHARED_AXIS_MOVE]"
            f" axis={getattr(rot_shared.drag_axis,'axis',None)}"
            f" owner={owner}"
            f" den={den_dbg:.6f}"
            f" cur_dir=({cur_dir.x():.3f},{cur_dir.y():.3f},{cur_dir.z():.3f})"
        )

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_qnew(self, *, rot_shared, cur_dir, _rot_dbg):
        qnew = rot_shared.update_axis_drag(cur_dir)
        if qnew is None:
            _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] qnew None")
            return None
        try:
            if hasattr(qnew, "normalized"):
                qnew = qnew.normalized()
        except Exception:
            pass
        return qnew

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ray(self, mp, _rot_dbg):
        dpr, vw, vh, px, py = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_viewport(mp=mp)
        invPV = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_invpv(
            dpr=dpr,
            vw=vw,
            vh=vh,
            _rot_dbg=_rot_dbg,
        )
        if invPV is None:
            return None
        return self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_unproject(
            invPV=invPV,
            px=px,
            py=py,
            vw=vw,
            vh=vh,
            _rot_dbg=_rot_dbg,
        )

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_viewport(self, *, mp):
        dpr = float(getattr(self, "_rot_shared_dpr", 0.0) or 0.0)
        if dpr <= 0.0:
            dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())

        vw = float(getattr(self, "_rot_shared_vw", 0.0) or 0.0)
        vh = float(getattr(self, "_rot_shared_vh", 0.0) or 0.0)
        if vw <= 1.0:
            vw = float(self.width()) * dpr
        if vh <= 1.0:
            vh = float(self.height()) * dpr

        # IMPORTANT: compute mouse in the same pixel space as vw/vh.
        px = float(mp.x()) * dpr
        py = float(mp.y()) * dpr
        return dpr, vw, vh, px, py

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_invpv(self, *, dpr, vw, vh, _rot_dbg):
        # Prefer cached viewport + dpr from mousePressEvent so unproject stays stable during drag.
        invPV = getattr(self, "_rot_shared_invPV", None)
        if invPV is not None:
            return invPV

        renderer = getattr(self, "_mgl_renderer", None)
        Pn = getattr(renderer, "_mgl_pick_proj", None) if renderer is not None else None
        Vn = getattr(renderer, "_mgl_pick_view", None) if renderer is not None else None
        Mn = getattr(renderer, "_mgl_pick_model", None) if renderer is not None else None

        if np is None or Pn is None or Vn is None or Mn is None:
            _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] missing invPV and missing P/V/M")
            return None

        PV = (
            np.asarray(Pn, dtype=np.float32)
            @ np.asarray(Vn, dtype=np.float32)
            @ np.asarray(Mn, dtype=np.float32)
        ).astype(np.float32)

        try:
            invPV = np.linalg.inv(PV)
        except Exception as ex:
            _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] invPV " + repr(ex))
            return None

        # cache so subsequent move events do not need P/V/M
        self._rot_shared_invPV = invPV
        self._rot_shared_vw = vw
        self._rot_shared_vh = vh
        self._rot_shared_dpr = dpr
        return invPV

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_unproject(self, *, invPV, px, py, vw, vh, _rot_dbg):
        ray = _gv_ray_from_screen(invPV=invPV, px=px, py=py, vw=vw, vh=vh)
        if ray is None:
            _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] ray len too small")
            return None
        ray_o, ray_d_np = ray

        # Ray origin for the unprojected ray (near point is on the ray).
        cam = QtGui.QVector3D(float(ray_o[0]), float(ray_o[1]), float(ray_o[2]))
        ray_d = QtGui.QVector3D(float(ray_d_np[0]), float(ray_d_np[1]), float(ray_d_np[2]))
        return cam, ray_d

    def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ring_dir(self, rot_shared, cam, ray_d, _rot_dbg):
        center_w = getattr(self, "_rot_shared_axis_center_world", None)
        axis_world = getattr(rot_shared.drag_axis, "axis_world", None)

        if not isinstance(center_w, QtGui.QVector3D) or not isinstance(axis_world, QtGui.QVector3D):
            _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] missing center_w/axis_world")
            return None

        if axis_world.length() > 1e-6:
            axis_world = axis_world / axis_world.length()

        # Smoketest-style: closest point on ray to gizmo center, then project onto ring plane
        cur_dir = self._axis_ring_dir_world(
            cam=cam,
            ray_d=ray_d,
            center_w=center_w,
            axis_world=axis_world,
        )

        # continuity: keep cur_dir on the same hemisphere as last_dir (prevents 180 flips / wobble)
        last_dir = getattr(rot_shared.drag_axis, "last_dir", None)
        if isinstance(last_dir, QtGui.QVector3D):
            if float(QtGui.QVector3D.dotProduct(last_dir, cur_dir)) < 0.0:
                cur_dir = QtGui.QVector3D(-cur_dir.x(), -cur_dir.y(), -cur_dir.z())

        # store for next move
        try:
            rot_shared.drag_axis.last_dir = QtGui.QVector3D(cur_dir)
        except Exception:
            pass
        return cur_dir, axis_world

    def _handle_mouse_move_moderngl_rot_shared_axis_apply(self, owner, rot_shared, qnew, _rot_dbg):
        # APPLY: use quaternion result so rings stay constrained (no wobble)
        try:
            qapply = self._handle_mouse_move_moderngl_rot_shared_axis_apply_qapply(rot_shared=rot_shared, qnew=qnew)
            self._handle_mouse_move_moderngl_rot_shared_axis_apply_cache_quat(owner=owner, qapply=qapply)
            rx0, ry0, rz0, candidates = self._handle_mouse_move_moderngl_rot_shared_axis_apply_candidates(qapply=qapply)
            best = self._handle_mouse_move_moderngl_rot_shared_axis_apply_best(
                owner=owner,
                rx0=rx0,
                ry0=ry0,
                rz0=rz0,
                candidates=candidates,
            )
            self._handle_mouse_move_moderngl_rot_shared_axis_apply_set_owner(owner=owner, best=best)
        except Exception as ex:
            _rot_dbg("[ROT_SHARED_AXIS_APPLY_ERR] " + repr(ex))

    def _handle_mouse_move_moderngl_rot_shared_axis_apply_qapply(self, *, rot_shared, qnew):
        qapply = qnew
        start_rot = getattr(rot_shared.drag_axis, "start_rot", None)
        if start_rot is not None:
            try:
                qdelta = (qnew * start_rot.conjugated()).normalized()
                qapply = (qdelta.conjugated() * start_rot).normalized()
            except Exception:
                qapply = qnew
        return qapply

    def _handle_mouse_move_moderngl_rot_shared_axis_apply_cache_quat(self, *, owner, qapply):
        try:
            self._rot_owner_quat[owner] = qapply
        except Exception:
            pass

    def _handle_mouse_move_moderngl_rot_shared_axis_apply_candidates(self, *, qapply):
        rx0, ry0, rz0 = self._rot_shared_euler_deg_from_q(qapply)
        candidates = [
            (float(rx0), float(ry0), float(rz0)),
            (float(rx0) + 180.0, 180.0 - float(ry0), float(rz0) + 180.0),
            (float(rx0) - 180.0, 180.0 - float(ry0), float(rz0) - 180.0),
        ]
        return rx0, ry0, rz0, candidates

    def _handle_mouse_move_moderngl_rot_shared_axis_apply_best(self, *, owner, rx0, ry0, rz0, candidates):
        cur_rot_deg, _ = self._get_owner_rot_deg(owner)
        current_xyz = (
            float(cur_rot_deg[0]),
            float(cur_rot_deg[1]),
            float(cur_rot_deg[2]),
        )
        best = _gv_closest_unwrapped_euler(candidates=candidates, current_xyz=current_xyz)
        if best is None:
            cx, cy, cz = current_xyz
            return (self._unwrap_deg(cx, rx0), self._unwrap_deg(cy, ry0), self._unwrap_deg(cz, rz0))
        return best

    def _handle_mouse_move_moderngl_rot_shared_axis_apply_set_owner(self, *, owner, best):
        self._set_owner_rot_deg(
            owner,
            best,
            bool(getattr(self, "_rot_shared_is_splat", False)),
        )

    def _handle_mouse_move_moderngl_xform_drag(self, e):
        if getattr(self, "_xform_dragging", False) and (e.buttons() & QtCore.Qt.LeftButton):
            try:
                ctx = self._handle_mouse_move_moderngl_xform_drag_context(e=e)
                if ctx is None:
                    return True

                if ctx["drag_mode"] == "scale":
                    return self._handle_mouse_move_moderngl_xform_scale_drag(e=e, ctx=ctx)

                return self._handle_mouse_move_moderngl_xform_translate_drag(e=e, ctx=ctx)

            except Exception as exc:
                print("[GIZMO_DRAG] failed:", exc, flush=True)

        return False

    def _handle_mouse_move_moderngl_xform_drag_context(self, *, e):
        if np is None:
            return None

        axis = getattr(self, "_xform_drag_axis", None)
        owner = getattr(self, "_xform_drag_owner", None)
        g0 = getattr(self, "_xform_drag_start_pos", None)
        if axis is None or owner is None or g0 is None:
            return None

        dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
        px = float(e.x()) * dpr
        py = float(e.y()) * dpr
        vw = float(self.width()) * dpr
        vh = float(self.height()) * dpr

        renderer = getattr(self, "_mgl_renderer", None) or self
        return {
            "axis": axis,
            "owner": owner,
            "g0": g0,
            "px": px,
            "py": py,
            "vw": vw,
            "vh": vh,
            "renderer": renderer,
            "P": getattr(renderer, "_mgl_pick_proj", None),
            "V": getattr(renderer, "_mgl_pick_view", None),
            "M": getattr(renderer, "_mgl_pick_model", None),
            "drag_mode": getattr(self, "_xform_drag_mode", "translate") or "translate",
        }

    def _handle_mouse_move_moderngl_xform_ray(self, *, px, py, vw, vh, P, V, M):
        if P is None or V is None or M is None:
            return None
        try:
            PV = (P @ V @ M).astype("f4")
            invPV = np.linalg.inv(PV)
            return _gv_ray_from_screen(invPV=invPV, px=px, py=py, vw=vw, vh=vh)
        except Exception:
            return None

    def _handle_mouse_move_moderngl_xform_translate_drag(
        self,
        e,
        ctx,
    ):
        ray = self._handle_mouse_move_moderngl_xform_ray(
            px=ctx["px"],
            py=ctx["py"],
            vw=ctx["vw"],
            vh=ctx["vh"],
            P=ctx["P"],
            V=ctx["V"],
            M=ctx["M"],
        )
        if ray is None:
            return True
        ray_o, ray_d = ray

        new_pos = self._handle_mouse_move_moderngl_xform_translate_compute_pos(
            axis=ctx["axis"],
            owner=ctx["owner"],
            g0=ctx["g0"],
            ray_o=ray_o,
            ray_d=ray_d,
            V=ctx["V"],
            M=ctx["M"],
        )

        if new_pos is None:
            return True

        self._handle_mouse_move_moderngl_xform_translate_apply_owner(
            new_pos=new_pos,
            owner=ctx["owner"],
            renderer=ctx["renderer"],
        )

        # redraw
        self.update()
        e.accept()
        return True

    def _handle_mouse_move_moderngl_xform_translate_compute_pos(
        self,
        axis,
        owner,
        g0,
        ray_o,
        ray_d,
        V,
        M,
    ):
        if axis == "view":
            return self._handle_mouse_move_moderngl_xform_translate_compute_pos_view(
                g0=g0,
                ray_o=ray_o,
                ray_d=ray_d,
                V=V,
                M=M,
            )

        return self._handle_mouse_move_moderngl_xform_translate_compute_pos_axis(
            axis=axis,
            owner=owner,
            g0=g0,
            ray_o=ray_o,
            ray_d=ray_d,
        )

    def _handle_mouse_move_moderngl_xform_translate_compute_pos_view(self, *, g0, ray_o, ray_d, V, M):
        nrm = getattr(self, "_xform_drag_plane_normal", None)
        if nrm is None:
            nrm = _gv_plane_normal_from_vm(V=V, M=M)
        start_hit = getattr(self, "_xform_drag_plane_start", None)
        if nrm is not None:
            hit = _gv_plane_hit(point_on_plane=g0, plane_normal=nrm, ray_o=ray_o, ray_d=ray_d)
            if hit is not None:
                if start_hit is None:
                    self._xform_drag_plane_start = hit
                    start_hit = hit
                delta = hit - start_hit
                return g0 + delta
        return None

    def _handle_mouse_move_moderngl_xform_translate_compute_pos_axis(self, *, axis, owner, g0, ray_o, ray_d):
        a = self._handle_mouse_move_moderngl_xform_translate_axis_world(axis=axis, owner=owner)
        if a is None:
            return None

        s = self._handle_mouse_move_moderngl_xform_translate_axis_param(
            axis_world=a,
            g0=g0,
            ray_o=ray_o,
            ray_d=ray_d,
        )

        # On first move after pick, capture s0.
        s0 = getattr(self, "_xform_drag_s0", None)
        if s0 is None:
            self._xform_drag_s0 = s
            s0 = s

        delta = (float(s0) - s) * a
        return g0 + delta

    def _handle_mouse_move_moderngl_xform_translate_axis_world(self, *, axis, owner):
        a = getattr(self, "_xform_drag_axis_world", None)
        if a is None:
            a = self._handle_mouse_press_moderngl_left_gizmo_axis_local(axis)
            if a is None:
                return None
            if bool(getattr(self, "_xform_use_local", False)):
                R = self._handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(owner=owner)
                a = (R[:3, :3] @ a).astype(np.float32)

        if isinstance(a, QtGui.QVector3D):
            a = np.array([a.x(), a.y(), a.z()], dtype="f4")

        a = np.asarray(a, dtype="f4")
        al = float(np.linalg.norm(a))
        if al > 1e-8:
            a = a / al
        return a

    def _handle_mouse_move_moderngl_xform_translate_axis_param(self, *, axis_world, g0, ray_o, ray_d):
        # Compute parameter "s" along axis line closest to the mouse ray.
        return _gv_axis_line_ray_param(axis_world=axis_world, g0=g0, ray_o=ray_o, ray_d=ray_d)

    def _handle_mouse_move_moderngl_xform_translate_apply_owner(self, new_pos, owner, renderer):
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

    def _handle_mouse_move_moderngl_xform_scale_drag(
        self,
        e,
        ctx,
    ):
        try:
            start_scl = self._handle_mouse_move_moderngl_xform_scale_get_start_scl(owner=ctx["owner"])
            is_splat = bool(getattr(self, "_xform_drag_kind", None) == "splat")

            if ctx["axis"] == "u":
                new_scl = self._handle_mouse_move_moderngl_xform_scale_uniform_drag(
                    start_scl=start_scl,
                    px=ctx["px"],
                )
            else:
                new_scl = self._handle_mouse_move_moderngl_xform_scale_axis_drag(
                    axis=ctx["axis"],
                    owner=ctx["owner"],
                    g0=ctx["g0"],
                    px=ctx["px"],
                    py=ctx["py"],
                    vw=ctx["vw"],
                    vh=ctx["vh"],
                    P=ctx["P"],
                    V=ctx["V"],
                    M=ctx["M"],
                    start_scl=start_scl,
                )
                if new_scl is None:
                    return True

            self._handle_mouse_move_moderngl_xform_scale_apply_owner(
                e=e,
                owner=ctx["owner"],
                new_scl=new_scl,
                is_splat=is_splat,
            )
            return True
        except Exception as exc:
            print("[GIZMO_SCALE] failed:", exc, flush=True)
            return True

    def _handle_mouse_move_moderngl_xform_scale_get_start_scl(self, owner):
        start_scl = getattr(self, "_xform_drag_start_scl", None)
        if start_scl is None:
            try:
                start_scl, _ = self._get_owner_scl(owner)
            except Exception:
                start_scl = (1.0, 1.0, 1.0)
            self._xform_drag_start_scl = start_scl
        return start_scl

    def _handle_mouse_move_moderngl_xform_scale_uniform_drag(self, start_scl, px):
        start_px = getattr(self, "_xform_scale_start_px", None)
        if start_px is None:
            try:
                start_px = float(px)
                self._xform_scale_start_px = start_px
            except Exception:
                start_px = float(px)

        dx = float(px) - float(start_px)
        # Horizontal-only uniform scale: right = bigger, left = smaller.
        sensitivity = 0.0020
        factor = 1.0 + (dx * sensitivity)
        factor = max(0.01, float(factor))
        return (
            max(0.01, float(start_scl[0]) * factor),
            max(0.01, float(start_scl[1]) * factor),
            max(0.01, float(start_scl[2]) * factor),
        )

    def _handle_mouse_move_moderngl_xform_scale_axis_drag(
        self,
        axis,
        owner,
        g0,
        px,
        py,
        vw,
        vh,
        P,
        V,
        M,
        start_scl,
    ):
        if P is None or V is None or M is None:
            return None

        axis_world = self._handle_mouse_move_moderngl_xform_scale_axis_world(
            axis=axis,
            owner=owner,
        )
        if axis_world is None:
            return None

        factor = self._handle_mouse_move_moderngl_xform_scale_axis_factor(
            axis_world=axis_world,
            g0=g0,
            px=px,
            py=py,
            vw=vw,
            vh=vh,
            P=P,
            V=V,
            M=M,
        )
        if factor is None:
            return None

        new_scl = [float(start_scl[0]), float(start_scl[1]), float(start_scl[2])]
        idx = 0 if axis == "x" else (1 if axis == "y" else 2)
        new_scl[idx] = max(0.01, new_scl[idx] * factor)
        return tuple(new_scl)

    def _handle_mouse_move_moderngl_xform_scale_axis_world(self, axis, owner):
        axis_world = getattr(self, "_xform_scale_axis_world", None)
        if axis_world is None:
            axis_local = self._handle_mouse_press_moderngl_left_gizmo_axis_local(axis)
            if axis_local is None:
                return None
            axis_world = axis_local

            if bool(getattr(self, "_xform_use_local", False)):
                R = self._handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(owner=owner)
                axis_world = (R[:3, :3] @ axis_local).astype(np.float32)

        if isinstance(axis_world, QtGui.QVector3D):
            axis_world = np.array([axis_world.x(), axis_world.y(), axis_world.z()], dtype="f4")

        axis_world = np.asarray(axis_world, dtype=np.float32)
        ln = float(np.linalg.norm(axis_world))
        if ln <= 1e-8:
            return None

        axis_world = axis_world / ln
        self._xform_scale_axis_world = axis_world
        return axis_world

    def _handle_mouse_move_moderngl_xform_scale_axis_factor(self, axis_world, g0, px, py, vw, vh, P, V, M):
        ray = self._handle_mouse_move_moderngl_xform_ray(
            px=px,
            py=py,
            vw=vw,
            vh=vh,
            P=P,
            V=V,
            M=M,
        )
        if ray is None:
            return None
        ray_o, ray_d = ray

        w0 = ray_o - g0
        ad = float(np.dot(axis_world, ray_d))
        denom = 1.0 - ad * ad
        if abs(denom) < 1e-6:
            s = float(np.dot(axis_world, w0))
        else:
            s = float((ad * float(np.dot(ray_d, w0)) - float(np.dot(axis_world, w0))) / denom)

        s0 = getattr(self, "_xform_drag_s0", None)
        if s0 is None:
            self._xform_drag_s0 = s
            s0 = s

        if abs(float(s0)) > 1e-6:
            factor = float(s) / float(s0)
        else:
            factor = 1.0
        return max(0.01, float(factor))

    def _handle_mouse_move_moderngl_xform_scale_apply_owner(self, e, owner, new_scl, is_splat):
        self._mgl_set_scene_asset_xform(
            owner,
            scl=new_scl,
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

    def _handle_mouse_move_moderngl_arcball_drag(self, e):
        if self._mgl_arcball is not None and (e.buttons() & QtCore.Qt.LeftButton):
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    return True
            except Exception:
                pass
            if bool(getattr(self, "_mgl_orbit_locked", True)):
                if not self._mgl_orbit_dragging:
                    self._sync_locked_orbit_from_arcball()
                    self._mgl_orbit_dragging = True
                    self._mgl_orbit_last_pos = e.pos()
                delta = e.pos() - self._mgl_orbit_last_pos
                self._mgl_orbit_last_pos = e.pos()
                self._mgl_orbit_yaw -= float(delta.x()) * self._orbit_sensitivity
                self._mgl_orbit_pitch += float(delta.y()) * self._orbit_sensitivity
                if self._mgl_orbit_pitch > math.pi:
                    self._mgl_orbit_pitch -= (2.0 * math.pi)
                elif self._mgl_orbit_pitch < -math.pi:
                    self._mgl_orbit_pitch += (2.0 * math.pi)
                self._apply_locked_orbit()
            else:
                self._mgl_arcball.onDrag(e.x(), e.y())
            self.update()  # ensure pick matrices stay fresh
            e.accept()
            return True
        return False

    def _handle_mouse_move_moderngl_pan_drag(self, e):
        if e.buttons() & QtCore.Qt.MiddleButton and self._mgl_center is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    return True
            except Exception:
                pass
            dx = e.x() - self._mgl_prev_x
            dy = e.y() - self._mgl_prev_y
            pan_scale = self._handle_mouse_move_moderngl_pan_drag_scale()
            right, up = self._handle_mouse_move_moderngl_pan_drag_basis()
            delta = (-dx * pan_scale) * right + (dy * pan_scale) * up
            self._mgl_center += delta
            self._mgl_prev_x = e.x()
            self._mgl_prev_y = e.y()
            self.update()
            e.accept()
            return True
        return False

    def _handle_mouse_move_moderngl_pan_drag_scale(self):
        pan_scale = float(getattr(self, "_mgl_pan_base", 0.01))
        try:
            zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
        except Exception:
            zoom = 0.0
        ref_zoom = getattr(self, "_mgl_pan_ref_zoom", None)
        try:
            ref_zoom = float(ref_zoom) if ref_zoom is not None else 0.0
        except Exception:
            ref_zoom = 0.0
        if not math.isfinite(ref_zoom) or ref_zoom <= 0.0:
            try:
                ref_zoom = float(self._mgl_camera_distance(self._mgl_fov))
            except Exception:
                ref_zoom = max(1e-6, zoom)
            try:
                self._mgl_pan_ref_zoom = ref_zoom
            except Exception:
                pass
        if ref_zoom > 0.0:
            ratio = zoom / ref_zoom if zoom > 0.0 else 0.0
            if ratio > 1.0:
                try:
                    exp_out = float(getattr(self, "_mgl_pan_zoom_exp_out", 1.2))
                except Exception:
                    exp_out = 1.2
                if exp_out <= 0.0:
                    exp_out = 1.0
                try:
                    boost = float(getattr(self, "_mgl_pan_zoom_boost", 10.0))
                except Exception:
                    boost = 10.0
                if boost < 1.0:
                    boost = 1.0
                try:
                    threshold = float(getattr(self, "_mgl_pan_zoom_threshold", 2.0))
                except Exception:
                    threshold = 2.0
                if threshold <= 1.0:
                    threshold = 1.0
                ramp = (ratio - 1.0) / (threshold - 1.0) if threshold > 1.0 else 1.0
                ramp = max(0.0, min(1.0, ramp))
                pan_scale *= (ratio ** exp_out) * (1.0 + (boost - 1.0) * ramp)
        return pan_scale

    def _handle_mouse_move_moderngl_pan_drag_basis(self):
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
        return right, up

    def _handle_mouse_move_moderngl_zoom_drag(self, e):
        if e.buttons() & QtCore.Qt.RightButton and self._mgl_zoom_press_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    return True
            except Exception:
                pass
            dx = e.pos().x() - self._mgl_zoom_press_pos.x()
            dy = e.pos().y() - self._mgl_zoom_press_pos.y()
            try:
                infinite = bool(getattr(self, "_mgl_zoom_infinite", True))
            except Exception:
                infinite = True
            if infinite:
                self._handle_mouse_move_moderngl_zoom_drag_infinite(dx=dx, dy=dy)
            else:
                self._handle_mouse_move_moderngl_zoom_drag_finite(dx=dx, dy=dy, e=e)
            self.update()
            e.accept()
            return True
        return False

    def _handle_mouse_move_moderngl_zoom_drag_infinite(self, *, dx, dy):
        cam_start = getattr(self, "_mgl_zoom_cam_start", None)
        cam_dir = getattr(self, "_mgl_zoom_cam_dir", None)
        ray_dir = getattr(self, "_mgl_zoom_ray_dir", None)
        center_start = getattr(self, "_mgl_zoom_center_start", None)
        scale = float(getattr(self, "_mgl_zoom_pan_scale", 0.02))
        delta = (dx - dy) * scale

        if cam_start is not None and cam_dir is not None and ray_dir is not None:
            if np is not None:
                cs = np.array(cam_start, dtype=np.float32)
                rd = np.array(ray_dir, dtype=np.float32)
                cd = np.array(cam_dir, dtype=np.float32)
                cam_pos = cs + (rd * float(delta))
                self._mgl_center = (cam_pos - cd * float(self._mgl_camera_zoom)).astype("f4")
            else:
                csx, csy, csz = float(cam_start[0]), float(cam_start[1]), float(cam_start[2])
                rdx, rdy, rdz = float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])
                cdx, cdy, cdz = float(cam_dir[0]), float(cam_dir[1]), float(cam_dir[2])
                cam_x = csx + rdx * float(delta)
                cam_y = csy + rdy * float(delta)
                cam_z = csz + rdz * float(delta)
                z = float(self._mgl_camera_zoom)
                self._mgl_center = (cam_x - cdx * z, cam_y - cdy * z, cam_z - cdz * z)
            return

        if ray_dir is not None and center_start is not None:
            if np is not None:
                c0 = np.array(center_start, dtype=np.float32)
                rd = np.array(ray_dir, dtype=np.float32)
                self._mgl_center = (c0 + rd * float(delta)).astype("f4")
            else:
                cx, cy, cz = float(center_start[0]), float(center_start[1]), float(center_start[2])
                rdx, rdy, rdz = float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])
                self._mgl_center = (
                    cx + rdx * float(delta),
                    cy + rdy * float(delta),
                    cz + rdz * float(delta),
                )

    def _handle_mouse_move_moderngl_zoom_drag_finite(self, *, dx, dy, e):
        distance = dx - dy
        exponent = abs(distance) / self._drag_divisor
        base = self._zoom_multiplier
        factor = base ** exponent
        start = self._mgl_zoom_start if self._mgl_zoom_start is not None else self._mgl_camera_zoom
        if distance > 0:
            target = start / factor
        else:
            target = start * factor
        min_zoom = float(getattr(self, "_mgl_min_zoom", 0.001))
        self._mgl_camera_zoom = max(min_zoom, min(10000.0, target))
        self._mgl_zoom_start = self._mgl_camera_zoom
        self._mgl_zoom_press_pos = e.pos()

    def _handle_mouse_move_example_pipeline(self, e):
        if self._use_example_pipeline:
            if self._handle_mouse_move_example_pipeline_orbit(e):
                return True
            if self._handle_mouse_move_example_pipeline_pan(e):
                return True
            if self._handle_mouse_move_example_pipeline_dolly(e):
                return True
        return False

    def _handle_mouse_move_example_pipeline_orbit(self, e):
        if self._orbit_dragging and self._orbit_last_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._orbit_dragging = False
                    self._orbit_last_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return True
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.LeftButton):
                self._orbit_dragging = False
                self._orbit_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
            delta = e.pos() - self._orbit_last_pos
            self._orbit_last_pos = e.pos()
            self._example_orbit(QtCore.QPointF(delta))
            self.update()
            e.accept()
            return True
        return False

    def _handle_mouse_move_example_pipeline_pan(self, e):
        if self._pan_dragging and self._pan_last_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._pan_dragging = False
                    self._pan_last_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return True
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.MiddleButton):
                self._pan_dragging = False
                self._pan_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
            delta = e.pos() - self._pan_last_pos
            self._pan_last_pos = e.pos()
            self._example_pan(QtCore.QPointF(delta))
            self.update()
            e.accept()
            return True
        return False

    def _handle_mouse_move_example_pipeline_dolly(self, e):
        if self._dolly_dragging and self._dolly_press_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._dolly_dragging = False
                    self._dolly_press_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return True
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.RightButton):
                self._dolly_dragging = False
                self._dolly_press_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
            delta = e.pos() - self._dolly_press_pos
            self._dolly_press_pos = e.pos()
            self._example_zoom(-float(delta.y()) / 60.0)
            self.update()
            e.accept()
            return True
        return False

    def _handle_mouse_move_legacy_orbit(self, e):
        if self._orbit_dragging and self._orbit_last_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._orbit_dragging = False
                    self._orbit_last_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return True
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.LeftButton):
                self._orbit_dragging = False
                self._orbit_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
            delta = e.pos() - self._orbit_last_pos
            self._orbit_last_pos = e.pos()
            self._cam_yaw -= float(delta.x()) * self._orbit_sensitivity
            self._cam_pitch -= float(delta.y()) * self._orbit_sensitivity
            self._cam_pitch = max(-1.45, min(1.45, self._cam_pitch))
            self.update()
            e.accept()
            return True
        return False

    def _handle_mouse_move_legacy_pan(self, e):
        if self._pan_dragging and self._pan_last_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._pan_dragging = False
                    self._pan_last_pos = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return True
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.MiddleButton):
                self._pan_dragging = False
                self._pan_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
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
            return True
        return False

    def _handle_mouse_move_legacy_dolly(self, e):
        if self._dolly_dragging and self._dolly_press_pos is not None:
            try:
                if not (e.modifiers() & QtCore.Qt.AltModifier):
                    self._dolly_dragging = False
                    self._dolly_press_pos = None
                    self._dolly_start_dist = None
                    self.setCursor(QtCore.Qt.ArrowCursor)
                    e.accept()
                    return True
            except Exception:
                pass
            if not (e.buttons() & QtCore.Qt.RightButton):
                self._dolly_dragging = False
                self._dolly_press_pos = None
                self._dolly_start_dist = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
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
            return True
        return False

    def _handle_mouse_release_rot_shared_arc(self, e):
        if e.button() == QtCore.Qt.LeftButton and bool(getattr(self, "_rot_shared_arc_active", False)):
            self._rot_shared_arc_active = False
            self._commit_xform_history()
            self.update()
            e.accept()
            return True
        return False

    def _handle_mouse_release_rot_shared_view(self, e, rot_shared):
        # --- ROT_SHARED view-ring drag end ---
        if (
            e.button() == QtCore.Qt.LeftButton
            and rot_shared is not None
            and bool(getattr(rot_shared, "drag_view", False))
        ):
            try:
                rot_shared.end_view_ring_drag()
            except Exception:
                pass
            self._commit_xform_history()
            self.update()
            e.accept()
            return True
        return False

    def _handle_mouse_release_rot_shared_axis(self, e, rot_shared):
        # --- ROT_SHARED axis-ring drag end ---
        if (
            e.button() == QtCore.Qt.LeftButton
            and rot_shared is not None
            and bool(getattr(getattr(rot_shared, "drag_axis", None), "active", False))
        ):
            try:
                rot_shared.end_axis_drag()
            except Exception:
                pass
            self._rot_shared_axis_center_world = None
            self._rot_shared_axis = None
            self._rot_shared_axis_start_euler_deg = None
            self._rot_shared_axis_last_ang_deg = 0.0
            self._commit_xform_history()
            self.update()
            e.accept()
            return True
        return False


    def _handle_mouse_release_moderngl(self, e):
        if self._use_moderngl:
            if e.button() == QtCore.Qt.RightButton:
                self._handle_mouse_release_moderngl_right_button_nav()
            # --- 1) If we were dragging the gizmo, ALWAYS end that first ---
            if self._handle_mouse_release_moderngl_end_xform_drag(e):
                return True

            # --- 2) Normal click-pick (only if it was a click, not a drag) ---
            self._handle_mouse_release_moderngl_click_pick(e)

            # --- 3) Always end camera interactions cleanly ---
            self._handle_mouse_release_moderngl_end_camera_interactions(e)

            # Safety: if we somehow grabbed the mouse, release it now
            try:
                if QtWidgets.QApplication.mouseGrabber() is self:
                    self.releaseMouse()
            except Exception:
                pass

            self.setCursor(QtCore.Qt.ArrowCursor)
            super().mouseReleaseEvent(e)
            return True

        return False

    def _handle_mouse_release_moderngl_right_button_nav(self):
        try:
            orbit_enabled = bool(getattr(self, "_orbit_cam_enabled", True))
            fly_mode = bool(getattr(self, "_fly_mode_enabled", False))
            if orbit_enabled and bool(getattr(self, "_fps_camera_active", False)):
                self._fps_cam_sync_orbit_from_camera()
            if not fly_mode:
                self._fps_nav_active = False
                self._fps_nav_keys = set()
                self._fps_nav_look_last_pos = None
            else:
                self._fps_nav_look_last_pos = None
                try:
                    anchor = getattr(self, "_fps_nav_cursor_anchor", None)
                    if anchor is not None:
                        QtGui.QCursor.setPos(anchor)
                except Exception:
                    pass
                self._fps_nav_cursor_anchor = None
                self._fps_nav_warping = False
            if orbit_enabled:
                self._fps_camera_active = False
        except Exception:
            pass

    def _handle_mouse_release_moderngl_end_xform_drag(self, e):
        if not getattr(self, "_xform_dragging", False):
            return False

        self._commit_xform_history()
        self._handle_mouse_release_moderngl_log_splat_drag_end()
        self._handle_mouse_release_moderngl_reset_xform_drag_state()

        # Important: don't let a gizmo drag "fall through" into click-pick or orbit
        self._mgl_pick_press_pos = None

        # Safety: if we somehow grabbed the mouse, release it now
        self._handle_mouse_release_moderngl_release_mouse_grab()

        self.setCursor(QtCore.Qt.ArrowCursor)
        e.accept()
        return True

    def _handle_mouse_release_moderngl_log_splat_drag_end(self):
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

    def _handle_mouse_release_moderngl_reset_xform_drag_state(self):
        self._xform_dragging = False
        self._xform_drag_axis = None
        self._xform_drag_owner = None
        self._xform_drag_s0 = None
        self._xform_drag_kind = None
        self._xform_drag_mode = None
        self._xform_drag_start_scl = None
        self._xform_scale_start_dist = None
        self._xform_scale_start_px = None
        self._xform_scale_axis_world = None
        self._xform_scale_center_px = None
        self._xform_drag_axis_world = None
        self._xform_drag_plane_normal = None
        self._xform_drag_plane_start = None
        self._xform_gizmo_pos_locked = False

    def _handle_mouse_release_moderngl_release_mouse_grab(self):
        try:
            if QtWidgets.QApplication.mouseGrabber() is self:
                self.releaseMouse()
        except Exception:
            pass

    def _handle_mouse_release_moderngl_click_pick(self, e):
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
                                self._handle_mouse_release_moderngl_pick_owner(owner, renderer)
                            else:
                                self._handle_mouse_release_moderngl_pick_empty()
            except Exception:
                pass

    def _handle_mouse_release_moderngl_pick_owner(self, owner, renderer):
        self._handle_mouse_release_moderngl_pick_owner_select(owner=owner, renderer=renderer)
        self._handle_mouse_release_moderngl_pick_owner_place_gizmo(owner=owner, renderer=renderer)

        # Allow outliner edits to reposition the gizmo after selection.
        try:
            self._xform_gizmo_pos_locked = False
        except Exception:
            pass

        self.update()

    def _handle_mouse_release_moderngl_pick_owner_select(self, *, owner, renderer):
        w = self.window()
        if hasattr(w, "select_scene_asset"):
            w.select_scene_asset(owner)

        try:
            kind = getattr(renderer, "_mgl_last_pick_kind", None)
        except Exception:
            kind = None

        try:
            self._mgl_log("scene: pick owner=" + str(owner) + " kind=" + str(kind))
        except Exception:
            pass

        # Force gizmo to the owner pivot (stored xform) or bounds center fallback.
        self._xform_gizmo_owner = owner
        try:
            self._xform_gizmo_owner_kind = kind
        except Exception:
            self._xform_gizmo_owner_kind = None

    def _handle_mouse_release_moderngl_pick_owner_place_gizmo(self, *, owner, renderer):
        try:
            is_splat = self._handle_mouse_release_moderngl_pick_owner_is_splat(owner=owner, renderer=renderer)
            xf_pos = self._handle_mouse_release_moderngl_pick_owner_xf_pos(
                owner=owner,
                renderer=renderer,
                is_splat=is_splat,
            )

            if is_splat:
                pivot = self._handle_mouse_release_moderngl_pick_owner_splat_pivot(owner=owner, renderer=renderer)
                self._handle_mouse_release_moderngl_pick_owner_set_splat_pos(
                    owner=owner,
                    renderer=renderer,
                    xf_pos=xf_pos,
                    pivot=pivot,
                )
                try:
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
            else:
                # mesh: pos is already world pivot (even if zero)
                self._xform_gizmo_pos = (
                    float(xf_pos[0]),
                    float(xf_pos[1]),
                    float(xf_pos[2]),
                )
                self._xform_gizmo_pos_locked = True
        except Exception:
            pass

    def _handle_mouse_release_moderngl_pick_owner_is_splat(self, *, owner, renderer):
        is_splat = False
        try:
            splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
            if not isinstance(splat_map, dict) or not splat_map:
                splat_map = getattr(renderer, "_mgl_scene_splats", None)
            if isinstance(splat_map, dict) and owner in splat_map:
                is_splat = True
        except Exception:
            is_splat = False
        return is_splat

    def _handle_mouse_release_moderngl_pick_owner_xf_pos(self, *, owner, renderer, is_splat):
        xf = {}
        get_xf = (
            getattr(renderer, "_mgl_get_scene_splat_xform", None)
            if is_splat
            else getattr(renderer, "_mgl_get_scene_asset_xform", None)
        )
        if callable(get_xf):
            xf = get_xf(owner) or {}
        return tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))

    def _handle_mouse_release_moderngl_pick_owner_splat_pivot(self, *, owner, renderer):
        # For splats, xform.pos is an offset from the local pivot.
        try:
            bounds_map = (
                getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
            )
            if isinstance(bounds_map, dict) and owner in bounds_map:
                mins, maxs = bounds_map.get(owner) or (None, None)
                if mins is not None and maxs is not None:
                    return (
                        (float(mins[0]) + float(maxs[0])) * 0.5,
                        (float(mins[1]) + float(maxs[1])) * 0.5,
                        (float(mins[2]) + float(maxs[2])) * 0.5,
                    )
        except Exception:
            pass
        return None

    def _handle_mouse_release_moderngl_pick_owner_splat_bounds_center(self, *, owner, renderer):
        try:
            bounds_map = (
                getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
            )
            if isinstance(bounds_map, dict) and owner in bounds_map:
                mins, maxs = bounds_map.get(owner) or (None, None)
                if mins is not None and maxs is not None:
                    return (
                        (float(mins[0]) + float(maxs[0])) * 0.5,
                        (float(mins[1]) + float(maxs[1])) * 0.5,
                        (float(mins[2]) + float(maxs[2])) * 0.5,
                    )
        except Exception:
            pass
        return None

    def _handle_mouse_release_moderngl_pick_owner_set_splat_pos(self, *, owner, renderer, xf_pos, pivot):
        # splat xform.pos is an offset from local pivot
        if pivot is not None:
            self._xform_gizmo_pos = (
                float(xf_pos[0] + pivot[0]),
                float(xf_pos[1] + pivot[1]),
                float(xf_pos[2] + pivot[2]),
            )
            self._xform_gizmo_pos_locked = True
            return

        # fallback to bounds center
        center = self._handle_mouse_release_moderngl_pick_owner_splat_bounds_center(
            owner=owner,
            renderer=renderer,
        )
        if center is None:
            return
        self._xform_gizmo_pos = center
        self._xform_gizmo_pos_locked = True

    def _handle_mouse_release_moderngl_pick_empty(self):
        # Clicked empty space: clear selection + hide gizmo
        try:
            self._mgl_log("scene: click empty -> clear selection")
        except Exception:
            pass

        # Clear gizmo selection state
        self._xform_gizmo_owner = None
        self._xform_gizmo_owner_kind = None
        self._xform_gizmo_pos_locked = False
        self._xform_gizmo_pos = (0.0, 0.0, 0.0)

        # Stop any active rotate drags safely
        try:
            rot_shared = getattr(self, "_rot_shared", None)
            if rot_shared is not None:
                rot_shared.end_drag()
        except Exception:
            pass

        # Clear outliner selection if the window exposes the helper
        try:
            w = self.window()
            if w is not None and hasattr(w, "clear_scene_asset_selection"):
                w.clear_scene_asset_selection()
        except Exception:
            pass

        self.update()

    def _handle_mouse_release_moderngl_end_camera_interactions(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._mgl_pick_press_pos = None
            self._mgl_orbit_dragging = False
            self._mgl_orbit_last_pos = None
            if self._mgl_arcball is not None:
                try:
                    self._mgl_arcball.onClickLeftUp()
                except Exception:
                    pass

        if e.button() == QtCore.Qt.RightButton:
            self._mgl_zoom_press_pos = None
            self._mgl_zoom_start = None
            self._mgl_zoom_center_start = None
            self._mgl_zoom_cam_start = None
            self._mgl_zoom_cam_dir = None
            self._mgl_zoom_ray_dir = None

    def _handle_mouse_release_example_pipeline(self, e):
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
            return True
        return False

    def _handle_mouse_release_legacy(self, e):
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

    def mouseReleaseEvent(self, e):
        rot_shared = getattr(self, "_rot_shared", None)

        if self._handle_mouse_release_rot_shared_arc(e):
            return

        if self._handle_mouse_release_rot_shared_view(e, rot_shared):
            return

        if self._handle_mouse_release_rot_shared_axis(e, rot_shared):
            return


        if self._handle_mouse_release_moderngl(e):
            return

        # --- non-ModernGL paths unchanged ---
        if self._handle_mouse_release_example_pipeline(e):
            return

        self._handle_mouse_release_legacy(e)

    def wheelEvent(self, e):
        if bool(getattr(self, "_fps_nav_active", False)):
            try:
                delta = e.angleDelta().y() / 120.0
            except Exception:
                delta = 0.0
            if delta:
                try:
                    step = float(getattr(self, "_fps_nav_speed_step", 1.15))
                except Exception:
                    step = 1.15
                speed = float(getattr(self, "_fps_nav_speed", 2.0))
                speed *= step ** float(delta)
                try:
                    speed = max(float(getattr(self, "_fps_nav_speed_min", 0.1)), speed)
                    speed = min(float(getattr(self, "_fps_nav_speed_max", 50.0)), speed)
                except Exception:
                    pass
                self._fps_nav_speed = speed
                try:
                    self.update()
                except Exception:
                    pass
            try:
                e.accept()
            except Exception:
                pass
            return
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
                min_zoom = float(getattr(self, "_mgl_min_zoom", 0.001))
                if self._mgl_camera_zoom < min_zoom:
                    self._mgl_camera_zoom = min_zoom
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

    def _handle_viewport_hotkeys(self, e, *, require_no_text_focus: bool = True) -> bool:
        try:
            if e is None:
                return False
            key = e.key()
        except Exception:
            key = None
        if key is None:
            return False
        if bool(getattr(self, "_fps_nav_active", False)):
            return False
        if require_no_text_focus:
            try:
                fw = QtWidgets.QApplication.focusWidget()
                if isinstance(
                    fw,
                    (
                        QtWidgets.QLineEdit,
                        QtWidgets.QTextEdit,
                        QtWidgets.QPlainTextEdit,
                        QtWidgets.QSpinBox,
                        QtWidgets.QDoubleSpinBox,
                    ),
                ):
                    return False
            except Exception:
                pass

        mod_mask = (
            QtCore.Qt.ControlModifier
            | QtCore.Qt.AltModifier
            | QtCore.Qt.ShiftModifier
            | QtCore.Qt.MetaModifier
        )

        def _seq_to_keymods(seq: str):
            try:
                if not seq:
                    return None, None
                q = QtGui.QKeySequence(str(seq))
                if q.count() < 1:
                    return None, None
                val = int(q[0])
                mods = QtCore.Qt.KeyboardModifiers(int(val) & int(mod_mask))
                key = int(val) & ~int(mod_mask)
                if "shift+" not in str(seq).lower():
                    if QtCore.Qt.Key_A <= key <= QtCore.Qt.Key_Z:
                        mods = QtCore.Qt.KeyboardModifiers(int(mods) & ~int(QtCore.Qt.ShiftModifier))
                return key, mods
            except Exception:
                return None, None

        def _matches_hotkey(seq: str) -> bool:
            try:
                key, mods = _seq_to_keymods(seq)
                if key is None:
                    return False
                ev_mods = e.modifiers() if e is not None else QtCore.Qt.NoModifier
                ev_mods = QtCore.Qt.KeyboardModifiers(int(ev_mods) & int(mod_mask))
                if (e.key() == key) and (ev_mods == mods):
                    return True
                # Fallback for plain letter keys (handles Shift/Caps quirks).
                try:
                    s = str(seq or "").strip()
                    if len(s) == 1 and s.isalpha() and int(mods) == 0:
                        txt = str(e.text() or "")
                        if txt and txt.lower() == s.lower() and int(ev_mods) in (0, int(QtCore.Qt.ShiftModifier)):
                            return True
                except Exception:
                    pass
                return False
            except Exception:
                return False

        try:
            wire_seq = hotkeys_config.keyseq("gl_wireframe_toggle", "Shift+W")
            grid_seq = hotkeys_config.keyseq("gl_grid_toggle", "G")
        except Exception:
            wire_seq = "W"
            grid_seq = "G"

        if not (_matches_hotkey(wire_seq) or _matches_hotkey(grid_seq)):
            return False

        try:
            if e.isAutoRepeat():
                return True
        except Exception:
            pass

        if _matches_hotkey(wire_seq):
            try:
                toggle = getattr(self, "_mgl_wireframe_toggle", None)
                if toggle is not None:
                    toggle.setChecked(not bool(toggle.isChecked()))
                else:
                    self._on_mgl_wireframe_toggled(not bool(getattr(self, "_mgl_wireframe", False)))
            except Exception:
                pass
        else:
            try:
                checked = not bool(getattr(self, "_mgl_grid_visible", False))
                btn = getattr(self, "_grid_btn", None)
                if btn is not None:
                    btn.setChecked(bool(checked))
                self._on_grid_button_toggled(bool(checked))
            except Exception:
                pass
        try:
            e.accept()
        except Exception:
            pass
        return True

    def keyPressEvent(self, e):
        try:
            key = e.key()
        except Exception:
            key = None
        nav_key = None
        try:
            if key == QtCore.Qt.Key_W:
                nav_key = "w"
            elif key == QtCore.Qt.Key_A:
                nav_key = "a"
            elif key == QtCore.Qt.Key_S:
                nav_key = "s"
            elif key == QtCore.Qt.Key_D:
                nav_key = "d"
        except Exception:
            nav_key = None
        if nav_key is not None:
            fly_mode = bool(getattr(self, "_fly_mode_enabled", False))
            if bool(getattr(self, "_fps_nav_active", False)) or fly_mode:
                try:
                    if fly_mode:
                        self._fps_nav_active = True
                    self._fps_nav_keys.add(nav_key)
                except Exception:
                    pass
                try:
                    e.accept()
                except Exception:
                    pass
                return
        if key == QtCore.Qt.Key_Shift:
            if bool(getattr(self, "_fly_mode_enabled", False)) or bool(getattr(self, "_fps_nav_active", False)):
                try:
                    self._fps_nav_boost = True
                except Exception:
                    pass
                try:
                    e.accept()
                except Exception:
                    pass
                return
        if self._handle_viewport_hotkeys(e, require_no_text_focus=True):
            return
        if getattr(self, "_gizmo_hotkeys_active", False):
            super().keyPressEvent(e)
            return
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
        if key == QtCore.Qt.Key_E:
            self._xform_gizmo_mode = "scale"
            try:
                self.update()
            except Exception:
                pass
            e.accept()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        try:
            key = e.key()
        except Exception:
            key = None
        nav_key = None
        try:
            if key == QtCore.Qt.Key_W:
                nav_key = "w"
            elif key == QtCore.Qt.Key_A:
                nav_key = "a"
            elif key == QtCore.Qt.Key_S:
                nav_key = "s"
            elif key == QtCore.Qt.Key_D:
                nav_key = "d"
        except Exception:
            nav_key = None
        if nav_key is not None:
            try:
                self._fps_nav_keys.discard(nav_key)
            except Exception:
                pass
            if bool(getattr(self, "_fps_nav_active", False)) or bool(getattr(self, "_fly_mode_enabled", False)):
                try:
                    e.accept()
                except Exception:
                    pass
                return
        if key == QtCore.Qt.Key_Shift:
            try:
                self._fps_nav_boost = False
            except Exception:
                pass
            if bool(getattr(self, "_fps_nav_active", False)) or bool(getattr(self, "_fly_mode_enabled", False)):
                try:
                    e.accept()
                except Exception:
                    pass
                return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        # Keep gizmo when focus leaves the viewport (avoid hiding splats on UI click)
        try:
            try:
                self._mgl_log("scene: focus lost -> keep gizmo")
            except Exception:
                pass
            self._fps_nav_active = False
            self._fps_nav_keys = set()
            self._fps_nav_look_last_pos = None
            self._fps_nav_boost = False
            self.update()
        except Exception:
            pass
        super().focusOutEvent(e)
