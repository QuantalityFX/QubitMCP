from __future__ import annotations

import math
import json
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
from .gl_view_timeline import GraphGLTimelineMixin
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

def _qt_shift_active(mods) -> bool:
    try:
        return bool(mods & QtCore.Qt.ShiftModifier)
    except Exception:
        pass
    try:
        return bool(mods & QtCore.Qt.KeyboardModifier.ShiftModifier)
    except Exception:
        pass
    try:
        return bool(int(mods) & int(QtCore.Qt.ShiftModifier))
    except Exception:
        return False

class _TimelineAxisMouseFilter(QtCore.QObject):
    def __init__(self, view):
        super().__init__(view)
        self._view = view

    @staticmethod
    def _global_pos(ev):
        try:
            if hasattr(ev, "globalPosition"):
                gp = ev.globalPosition()
                if gp is not None:
                    return gp.toPoint()
        except Exception:
            pass
        try:
            if hasattr(ev, "globalPos"):
                gp = ev.globalPos()
                if gp is not None:
                    return gp
        except Exception:
            pass
        return None

    def eventFilter(self, obj, ev):
        try:
            if ev.type() != QtCore.QEvent.MouseButtonPress:
                return False
            if ev.button() != QtCore.Qt.LeftButton:
                return False
        except Exception:
            return False
        view = self._view
        if view is None:
            return False
        try:
            if not bool(getattr(view, "_timeline_enabled", False)):
                return False
        except Exception:
            return False
        panel = getattr(view, "_timeline_panel", None)
        if panel is None:
            return False
        try:
            if not panel.isVisible():
                return False
        except Exception:
            return False
        gp = self._global_pos(ev)
        if gp is None:
            return False
        try:
            panel_rect = QtCore.QRect(panel.mapToGlobal(QtCore.QPoint(0, 0)), panel.size())
            if not panel_rect.contains(gp):
                return False
        except Exception:
            pass
        labels = getattr(view, "_timeline_axis_labels", None)
        if isinstance(labels, list):
            for idx, lbl in enumerate(labels):
                if lbl is None:
                    continue
                try:
                    if not lbl.isVisible() or not lbl.isEnabled():
                        continue
                    rr = QtCore.QRect(lbl.mapToGlobal(QtCore.QPoint(0, 0)), lbl.size())
                except Exception:
                    continue
                if not rr.contains(gp):
                    continue
                try:
                    mods = QtWidgets.QApplication.keyboardModifiers()
                except Exception:
                    mods = QtCore.Qt.NoModifier
                additive = bool(_qt_shift_active(mods))
                try:
                    view._timeline_axis_log(
                        f"axis_global_hit axis={int(idx)} obj={type(obj).__name__} additive={int(bool(additive))}"
                    )
                except Exception:
                    pass
                try:
                    view._timeline_on_axis_label_clicked(int(idx), additive=bool(additive))
                except Exception as exc:
                    try:
                        view._timeline_axis_log(f"axis_global_error axis={int(idx)} err={exc!r}")
                    except Exception:
                        pass
                try:
                    ev.accept()
                except Exception:
                    pass
                return True
        try:
            view._timeline_axis_log(
                f"axis_global_miss obj={type(obj).__name__} x={int(gp.x())} y={int(gp.y())}",
                throttle_key="axis_global_miss",
                interval=0.35,
            )
        except Exception:
            pass
        return False

class GraphGLView(GraphGLTimelineMixin, MGLRendererMixin, QOpenGLWidget if QOpenGLWidget is not None else QtWidgets.QWidget):
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
        self._mgl_splat_shadow_prog = None
        self._mgl_splatq_quad_vbo = None   # static quad corners
        self._mgl_splatq_vbo = None        # instance buffer (Nx8)
        self._mgl_splatq_vao = None
        self._mgl_splat_shadow_vao = None
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
        self._mgl_shadow_prog = None
        self._mgl_shadow_fbo = None
        self._mgl_shadow_depth_tex = None
        self._mgl_shadow_quality = "low"
        self._mgl_shadow_render_quality = "high"
        self._mgl_shadow_map_size = 512
        self._mgl_shadow_size_current = 0
        self._mgl_shadows_enabled = True
        self._mgl_splat_cast_shadows_enabled = True
        self._mgl_shadow_bias = 0.0025
        self._mgl_shadow_darkness = 0.45
        self._mgl_shadow_light_dir = (0.35, 0.85, 0.45)
        self._mgl_shadow_light_mvp = None
        self._mgl_shadow_valid = False
        self._mgl_shadow_dirty = True
        self._mgl_shadow_signature = None
        self._mgl_shadow_last_update_ts = 0.0
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
        self._mgl_scene_pivot_local_by_owner: Dict[str, Tuple[float, float, float]] = {}
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
        self._mgl_grid_center_line_width = 3.0
        self._mgl_grid_center_color = (0.48, 0.48, 0.48, 0.90)
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
        self._cam_select_lock_icon_locked = None
        self._cam_select_lock_icon_unlocked = None
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
        self._camera_select_mode = "default"
        self._camera_select_lock_enabled = False
        self._camera_select_saved_default_state = None
        self._scene_camera_entries: List[Dict[str, object]] = []
        self._scene_camera_fov_by_owner: Dict[str, float] = {}
        self._scene_camera_aspect_by_owner: Dict[str, Tuple[int, int]] = {}
        self._cam_select_frame = None
        self._cam_select_combo = None
        self._cam_select_lock_btn = None
        self._cam_select_syncing = False
        self._timeline_panel = None
        self._timeline_h = 220
        self._timeline_enabled = False
        self._timeline_audio_panel = None
        self._timeline_audio_h = 88
        self._timeline_audio_enabled = False
        self._timeline_ignore_ui = False
        self._timeline_scene_name = "scene"
        self._timeline_owner_name = None
        self._timeline_manual_override_owners = set()
        self._timeline_owner_keys_cache = {}
        self._timeline_project_dir: Optional[Path] = None
        self._timeline_anim_path: Optional[Path] = None
        self._timeline_range_cfg_path: Optional[Path] = None
        self._timeline_audio_scene_name = "viewport"
        self._timeline_audio_cfg_path: Optional[Path] = None
        self._timeline_audio_path = ""
        self._timeline_audio_duration_ms = 0
        self._timeline_audio_wave_samples: List[float] = []
        self._timeline_audio_loaded_key = None
        self._timeline_audio_player = None
        self._timeline_audio_output = None
        self._timeline_audio_muted = False
        self._timeline_audio_file_label = None
        self._timeline_audio_mute_btn = None
        self._timeline_audio_wave_widget = None
        self._timeline_audio_status_label = None
        self._timeline_fps = 24.0
        self._timeline_keys: Dict[int, Dict[str, object]] = {}
        self._timeline_coord_x = None
        self._timeline_coord_y = None
        self._timeline_coord_z = None
        self._timeline_coord_rx = None
        self._timeline_coord_ry = None
        self._timeline_coord_rz = None
        self._timeline_axis_labels: List[QtWidgets.QLabel] = []
        self._timeline_curve_axes_filter: set[int] = set()
        self._timeline_axis_debug_logging = False
        self._timeline_axis_log_path = None
        self._timeline_axis_log_last: Dict[str, float] = {}
        self._timeline_coord_syncing = False
        self._timeline_tracks_frame = None
        self._timeline_playhead = None
        self._timeline_tracks_stack = None
        self._timeline_rows_host = None
        self._timeline_curves_canvas = None
        self._timeline_left_header_spacer = None
        self._timeline_area_widget = None
        self._timeline_track_rows: List[QtWidgets.QFrame] = []
        self._timeline_key_markers: List[List[QtWidgets.QFrame]] = []
        self._timeline_scrollbar = None
        self._timeline_tick_labels: List[QtWidgets.QLabel] = []
        self._timeline_target_label = None
        self._timeline_ticks_frame = None
        self._timeline_total_max = 240
        self._timeline_view_start = 0
        self._timeline_view_span = 120
        self._timeline_play_btn = None
        self._timeline_curves_btn = None
        self._timeline_material_live_btn = None
        self._timeline_fx_instances_btn = None
        self._timeline_handle_straight_btn = None
        self._timeline_handle_tied_btn = None
        self._timeline_handle_untied_btn = None
        self._timeline_curves_mode = False
        self._timeline_material_live_mode = True
        self._timeline_fx_instances_enabled = True
        self._timeline_fx_proxy_enabled = True
        self._timeline_material_last_frame = None
        self._timeline_material_fps_override = 0.0
        self._timeline_curve_min = -5.0
        self._timeline_curve_max = 5.0
        self._timeline_curve_selected = set()
        self._timeline_icons_loaded = False
        self._timeline_icon_play = None
        self._timeline_icon_stop = None
        self._timeline_icon_curve = None
        self._timeline_icon_curve_active = None
        self._timeline_icon_material_live = None
        self._timeline_icon_material_live_on = None
        self._timeline_icon_material_live_off = None
        self._timeline_icon_fx_switch = None
        self._timeline_icon_fx_switch_on = None
        self._timeline_icon_fx_switch_off = None
        self._timeline_icon_handle_straight = None
        self._timeline_icon_handle_tied = None
        self._timeline_icon_handle_untied = None
        self._timeline_icon_set_key = None
        self._timeline_icon_remove_key = None
        self._timeline_icon_loop = None
        self._timeline_icon_loop_on = None
        self._timeline_icon_loop_off = None
        self._timeline_keyframe_handle_path = None
        self._timeline_texture_seed = 0
        self._timeline_frame_spin = None
        self._timeline_texture_seed_spin = None
        self._timeline_frame_slider = None
        self._timeline_key_count_label = None
        self._timeline_mark_in_btn = None
        self._timeline_mark_out_btn = None
        self._timeline_loop_btn = None
        self._timeline_in_frame = None
        self._timeline_out_frame = None
        self._timeline_loop_enabled = True
        self._timeline_ui_timer = QtCore.QTimer(self)
        self._timeline_ui_timer.setInterval(250)
        self._timeline_ui_timer.timeout.connect(self._timeline_refresh_coord_labels)
        self._timeline_play_timer = QtCore.QTimer(self)
        self._timeline_play_timer.setInterval(33)  # 30 FPS playback
        self._timeline_play_timer.timeout.connect(self._timeline_on_play_tick)

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
        self._timeline_axis_mouse_filter = None
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                self._viewport_hotkey_filter = _ViewportHotkeyFilter(self)
                app.installEventFilter(self._viewport_hotkey_filter)
                self._timeline_axis_mouse_filter = _TimelineAxisMouseFilter(self)
                app.installEventFilter(self._timeline_axis_mouse_filter)
        except Exception:
            self._viewport_hotkey_filter = None
            self._timeline_axis_mouse_filter = None
        self._build_scale_controls()
        self._build_debug_toggle_button()
        self._build_debug_copy_button()
        self._build_camera_orbit_button()
        self._build_fly_mode_button()
        self._build_grid_button()
        self._build_zoom_mode_button()
        self._build_camera_selector_dropdown()
        


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
                texture_seed = settings.get("timeline_texture_seed", None)
                if texture_seed is not None:
                    self._apply_timeline_texture_seed(int(texture_seed), sync_ui=True, sync_scene=False)
        except Exception:
            pass
        try:
            self.set_timeline_scene_context()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_controls", None) is not None:
            h = int(getattr(self, "_controls_h", 44))
            self._controls.setGeometry(0, max(0, self.height() - h), self.width(), h)
        try:
            self._layout_timeline_panel()
        except Exception:
            pass
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
        cam_sel_frame = getattr(self, "_cam_select_frame", None)
        cam_sel_combo = getattr(self, "_cam_select_combo", None)
        if cam_sel_frame is not None and cam_sel_combo is not None:
            base_w = int(max(120, min(220, self.width() - (2 * self._side_btn_margin))))
            combo_h = 28
            ctrl_h = max(10, combo_h - 4)
            lock_btn = getattr(self, "_cam_select_lock_btn", None)
            lock_w = int(ctrl_h) if lock_btn is not None else 0
            gap = 2 if lock_w > 0 else 0
            combo_w = int(max(128, round(base_w * 0.65)))
            frame_w = int(combo_w + lock_w + gap + 4)
            combo_x = max(self._side_btn_margin, self.width() - self._side_btn_margin - frame_w)
            combo_y = self._side_btn_margin
            cam_sel_frame.setGeometry(combo_x, combo_y, frame_w, combo_h)
            cam_sel_combo.setGeometry(2, 2, combo_w, ctrl_h)
            if lock_btn is not None:
                lock_btn.setGeometry(2 + combo_w + gap, 2, lock_w, ctrl_h)
            try:
                self._update_camera_selector_lock_button()
            except Exception:
                pass
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
            self._camera_selector_release_unlocked_selection_on_navigation()
        except Exception:
            pass
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
        try:
            self._camera_selector_apply_fps_to_locked_owner(sync_ui=True)
        except Exception:
            pass

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
            try:
                self._camera_selector_apply_fps_to_locked_owner(sync_ui=True)
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
            self._build_timeline_panel()
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

    def _timeline_on_texture_seed_changed(self, value: int | None = None) -> None:
        if value is None:
            spin = getattr(self, "_timeline_texture_seed_spin", None)
            if spin is not None:
                try:
                    value = int(spin.value())
                except Exception:
                    value = None
        if value is None:
            return
        self._apply_timeline_texture_seed(int(value), sync_ui=False, sync_scene=True)

    def _apply_timeline_texture_seed(
        self,
        seed: int,
        *,
        sync_ui: bool = True,
        sync_scene: bool = True,
    ) -> None:
        try:
            seed = max(0, min(999999, int(seed)))
        except Exception:
            seed = 0
        self._timeline_texture_seed = int(seed)
        if sync_ui:
            spin = getattr(self, "_timeline_texture_seed_spin", None)
            if spin is not None:
                try:
                    spin.blockSignals(True)
                    spin.setValue(int(seed))
                finally:
                    try:
                        spin.blockSignals(False)
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
                    settings["timeline_texture_seed"] = int(seed)
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

    @staticmethod
    def _camera_casefold_get(mapping, key):
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

    def _load_camera_selector_lock_icons(self) -> None:
        if (
            getattr(self, "_cam_select_lock_icon_locked", None) is not None
            or getattr(self, "_cam_select_lock_icon_unlocked", None) is not None
        ):
            return
        icon_locked = None
        icon_unlocked = None
        try:
            root = Path(__file__).resolve().parents[2]
            locked_path = root / "icons" / "Locked_Icon.png"
            unlocked_path = root / "icons" / "Unlocked_Icon.png"
            if locked_path.exists():
                icon_locked = QtGui.QIcon(str(locked_path))
            if unlocked_path.exists():
                icon_unlocked = QtGui.QIcon(str(unlocked_path))
        except Exception:
            icon_locked = None
            icon_unlocked = None
        self._cam_select_lock_icon_locked = icon_locked
        self._cam_select_lock_icon_unlocked = icon_unlocked

    def _camera_selector_locked_owner(self) -> str:
        if not bool(getattr(self, "_camera_select_lock_enabled", False)):
            return ""
        owner = str(getattr(self, "_camera_select_mode", "default") or "").strip()
        if not owner or owner.lower() == "default":
            return ""
        return owner

    def _camera_selector_sync_fps_from_owner_pose(self, owner: str) -> bool:
        owner_key = str(owner or "").strip()
        if not owner_key or owner_key.lower() == "default":
            return False
        if np is None:
            return False
        pose = self._build_scene_camera_pose(owner_key)
        if pose is None:
            return False
        pos, fwd, up = pose
        cam = getattr(self, "_fps_camera", None)
        if cam is None:
            try:
                cam = FpsCamera()
            except Exception:
                return False
        try:
            cam.position = np.array(pos, dtype=np.float32)
            cam.forward = np.array(fwd, dtype=np.float32)
            cam.up = np.array(up, dtype=np.float32)
            if hasattr(cam, "_orthonormalize"):
                cam._orthonormalize()
            self._fps_camera = cam
        except Exception:
            return False
        try:
            fov = self._camera_casefold_get(getattr(self, "_scene_camera_fov_by_owner", {}), owner_key)
            if fov is not None:
                self._mgl_fov = max(5.0, min(170.0, float(fov)))
        except Exception:
            pass
        return True

    def _camera_selector_sync_fps_from_locked_owner(self) -> bool:
        owner = self._camera_selector_locked_owner()
        if not owner:
            return False
        return bool(self._camera_selector_sync_fps_from_owner_pose(owner))

    def _camera_selector_apply_fps_to_locked_owner(self, *, sync_ui: bool = False) -> bool:
        owner = self._camera_selector_locked_owner()
        if not owner:
            return False
        if np is None:
            return False
        cam = getattr(self, "_fps_camera", None)
        if cam is None:
            return False
        renderer = getattr(self, "_mgl_renderer", None) or self
        setf = getattr(renderer, "_mgl_set_scene_asset_xform", None)
        if not callable(setf):
            return False
        try:
            pos_v = np.array(getattr(cam, "position", (0.0, 0.0, 0.0)), dtype=np.float32).reshape(3)
        except Exception:
            return False
        try:
            fwd_v = np.array(getattr(cam, "forward", (0.0, 0.0, -1.0)), dtype=np.float32).reshape(3)
            fn = float(np.linalg.norm(fwd_v))
            if fn > 1.0e-6:
                fwd_v = fwd_v / fn
            else:
                fwd_v = np.array([0.0, 0.0, -1.0], dtype=np.float32)
        except Exception:
            fwd_v = np.array([0.0, 0.0, -1.0], dtype=np.float32)
        try:
            pitch = math.degrees(math.asin(max(-1.0, min(1.0, float(fwd_v[1])))))
            yaw = math.degrees(math.atan2(float(fwd_v[0]), float(-fwd_v[2])))
        except Exception:
            pitch = 0.0
            yaw = 0.0
        is_splat = False
        try:
            splat_map = getattr(renderer, "_mgl_scene_splats", None)
            if not isinstance(splat_map, dict) or not splat_map:
                splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
            if isinstance(splat_map, dict):
                if owner in splat_map:
                    is_splat = True
                else:
                    ol = owner.lower()
                    for k in splat_map.keys():
                        if str(k).strip().lower() == ol:
                            is_splat = True
                            break
        except Exception:
            is_splat = False
        try:
            setf(
                owner,
                pos=(float(pos_v[0]), float(pos_v[1]), float(pos_v[2])),
                # Match _build_scene_camera_pose inversion (pitch sign is flipped in scene xform convention).
                rot=(float(-pitch), float(yaw), 0.0),
                apply_to_scene_models=not bool(is_splat),
                use_splat_xform=bool(is_splat),
            )
        except Exception:
            return False
        if bool(sync_ui):
            try:
                w = self.window()
                if w is not None and hasattr(w, "update_scene_asset_xform"):
                    w.update_scene_asset_xform(owner)
            except Exception:
                pass
        return True

    def _update_camera_selector_lock_button(self) -> None:
        btn = getattr(self, "_cam_select_lock_btn", None)
        if btn is None:
            return
        mode = str(getattr(self, "_camera_select_mode", "default") or "default").strip()
        has_scene = bool(mode and mode.lower() != "default")
        locked = bool(getattr(self, "_camera_select_lock_enabled", False)) and bool(has_scene)
        if not has_scene:
            self._camera_select_lock_enabled = False
            locked = False
        self._load_camera_selector_lock_icons()
        icon = (
            getattr(self, "_cam_select_lock_icon_locked", None)
            if locked
            else getattr(self, "_cam_select_lock_icon_unlocked", None)
        )
        tooltip = (
            "Camera Lock: On (fly controls selected scene camera)"
            if locked
            else ("Camera Lock: Off (fly controls default camera)" if has_scene else "Select a scene camera to enable lock")
        )
        try:
            btn.blockSignals(True)
            btn.setEnabled(bool(has_scene))
            btn.setChecked(bool(locked))
            if icon is not None:
                btn.setIcon(icon)
                btn.setText("")
                inner = max(10, min(int(btn.width()), int(btn.height())) - 4)
                btn.setIconSize(QtCore.QSize(inner, inner))
            else:
                btn.setIcon(QtGui.QIcon())
                btn.setText("L")
            btn.setToolTip(tooltip)
        except Exception:
            pass
        finally:
            try:
                btn.blockSignals(False)
            except Exception:
                pass

    def _camera_selector_release_unlocked_selection_on_navigation(self) -> bool:
        try:
            if bool(getattr(self, "_camera_select_lock_enabled", False)):
                return False
        except Exception:
            return False
        try:
            mode = str(getattr(self, "_camera_select_mode", "default") or "default").strip().lower()
        except Exception:
            mode = "default"
        if not mode or mode == "default":
            return False
        interacting = False
        if not interacting:
            try:
                keys = getattr(self, "_fps_nav_keys", None)
                if isinstance(keys, (set, list, tuple)) and len(keys) > 0:
                    interacting = True
            except Exception:
                pass
        if not interacting:
            # `_fps_nav_active` can stay true while fly mode is idle; only treat
            # FPS nav as interaction when look-anchor state is currently active.
            try:
                if bool(getattr(self, "_fps_nav_active", False)):
                    interacting = getattr(self, "_fps_nav_cursor_anchor", None) is not None
            except Exception:
                interacting = False
        if not interacting:
            try:
                interacting = bool(getattr(self, "_mgl_orbit_dragging", False))
            except Exception:
                interacting = False
        if not interacting:
            try:
                interacting = getattr(self, "_mgl_zoom_press_pos", None) is not None
            except Exception:
                interacting = False
        if not interacting:
            try:
                interacting = bool(getattr(self, "_orbit_dragging", False))
            except Exception:
                interacting = False
        if not interacting:
            try:
                interacting = bool(getattr(self, "_pan_dragging", False))
            except Exception:
                interacting = False
        if not interacting:
            try:
                interacting = bool(getattr(self, "_dolly_dragging", False))
            except Exception:
                interacting = False
        if not interacting:
            return False
        # Manual navigation while unlocked means the user left camera look-through.
        try:
            self._camera_select_mode = "default"
        except Exception:
            pass
        try:
            self._camera_select_lock_enabled = False
        except Exception:
            pass
        try:
            self._camera_select_saved_default_state = None
        except Exception:
            pass
        try:
            self._refresh_camera_selector_dropdown()
        except Exception:
            pass
        return True

    def _on_camera_selector_lock_toggled(self, checked: bool) -> None:
        mode = str(getattr(self, "_camera_select_mode", "default") or "default").strip()
        has_scene = bool(mode and mode.lower() != "default")
        self._camera_select_lock_enabled = bool(checked) and bool(has_scene)
        if bool(self._camera_select_lock_enabled):
            try:
                self._camera_selector_sync_fps_from_locked_owner()
            except Exception:
                pass
        self._update_camera_selector_lock_button()

    def _build_camera_selector_dropdown(self) -> None:
        try:
            frame = QtWidgets.QFrame(self)
            frame.setObjectName("GLCamSelectFrame")
            frame.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            frame.setStyleSheet(
                "QFrame#GLCamSelectFrame{background:rgba(85,91,97,95);border:1px solid rgba(15,20,24,123);border-radius:4px;}"
                "QComboBox{background:rgba(15,18,22,104);color:#e2e8f0;border:1px solid rgba(15,20,24,123);border-radius:3px;padding:2px 18px 2px 6px;}"
                "QComboBox::drop-down{border:none;width:16px;}"
                "QComboBox QAbstractItemView{background:rgba(15,18,22,180);color:#e6edf3;border:1px solid rgba(60,68,80,200);outline:0px;}"
                "QComboBox QAbstractItemView::item:hover{background:rgba(31,41,55,200);}"
                "QComboBox QAbstractItemView::item:selected{background:#22c55e;color:#0f1216;}"
                "QToolButton#GLCamSelectLockButton{background:transparent;border:0px;}"
                "QToolButton#GLCamSelectLockButton:hover{background:transparent;border:0px;}"
                "QToolButton#GLCamSelectLockButton:checked{background:transparent;border:0px;}"
            )
            combo = QtWidgets.QComboBox(frame)
            combo.setObjectName("GLCamSelectCombo")
            combo.setToolTip("Viewport camera")
            combo.setMaxVisibleItems(8)
            try:
                lv = QtWidgets.QListView()
                lv.setMouseTracking(True)
                lv.setUniformItemSizes(True)
                combo.setView(lv)
            except Exception:
                pass

            # Force popup to anchor under the combo so it expands downward (same behavior as import versions).
            class _CamPopupDownFilter(QtCore.QObject):
                def __init__(self, cam_combo: QtWidgets.QComboBox):
                    super().__init__(cam_combo)
                    self._combo = cam_combo

                def eventFilter(self, obj, ev):
                    if ev.type() == QtCore.QEvent.Show:
                        QtCore.QTimer.singleShot(0, self._apply)
                    return False

                def _apply(self):
                    try:
                        cam_combo = self._combo
                        view = cam_combo.view()
                        popup = view.window()
                        pos = cam_combo.mapToGlobal(QtCore.QPoint(0, cam_combo.height()))
                        popup.move(pos)
                        popup.setFixedWidth(cam_combo.width())
                        view.setMinimumWidth(cam_combo.width())
                        popup.raise_()
                    except Exception:
                        pass

            combo._popup_down_filter = _CamPopupDownFilter(combo)  # keep alive
            try:
                combo.view().installEventFilter(combo._popup_down_filter)
            except Exception:
                pass

            lock_btn = QtWidgets.QToolButton(frame)
            lock_btn.setObjectName("GLCamSelectLockButton")
            lock_btn.setCheckable(True)
            lock_btn.setAutoRaise(True)
            lock_btn.setCursor(QtCore.Qt.PointingHandCursor)
            lock_btn.clicked.connect(self._on_camera_selector_lock_toggled)

            combo.currentIndexChanged.connect(self._on_camera_selector_changed)
            self._cam_select_frame = frame
            self._cam_select_combo = combo
            self._cam_select_lock_btn = lock_btn
            self._refresh_camera_selector_dropdown()
            frame.show()
            combo.show()
            lock_btn.show()
        except Exception:
            self._cam_select_frame = None
            self._cam_select_combo = None
            self._cam_select_lock_btn = None

    def _set_scene_camera_options(self, entries: List[Dict[str, object]] | None) -> None:
        clean: List[Dict[str, object]] = []
        fov_map: Dict[str, float] = {}
        aspect_map: Dict[str, Tuple[int, int]] = {}
        seen = set()
        for raw in entries or []:
            if not isinstance(raw, dict):
                continue
            owner = str(raw.get("owner") or "").strip()
            if not owner:
                continue
            key = owner.lower()
            if key in seen:
                continue
            seen.add(key)
            label = str(raw.get("label") or owner).strip() or owner
            fov_val = raw.get("fov", None)
            fov = None
            try:
                if fov_val is not None:
                    fov = float(fov_val)
            except Exception:
                fov = None
            aspect_width = None
            aspect_height = None
            try:
                aw = raw.get("aspect_width", None)
                if aw is not None:
                    aspect_width = int(float(aw))
            except Exception:
                aspect_width = None
            try:
                ah = raw.get("aspect_height", None)
                if ah is not None:
                    aspect_height = int(float(ah))
            except Exception:
                aspect_height = None
            if aspect_width is not None and aspect_width <= 0:
                aspect_width = None
            if aspect_height is not None and aspect_height <= 0:
                aspect_height = None
            clean.append(
                {
                    "owner": owner,
                    "label": label,
                    "fov": fov,
                    "aspect_width": aspect_width,
                    "aspect_height": aspect_height,
                }
            )
            if fov is not None:
                fov_map[owner] = fov
            if aspect_width is not None and aspect_height is not None:
                aspect_map[owner] = (int(aspect_width), int(aspect_height))
            # Keep UI to two options max: default + first scene camera.
            if len(clean) >= 1:
                break
        self._scene_camera_entries = clean
        self._scene_camera_fov_by_owner = fov_map
        self._scene_camera_aspect_by_owner = aspect_map
        if (not clean) and str(getattr(self, "_camera_select_mode", "default")) != "default":
            self._select_default_camera()
        self._refresh_camera_selector_dropdown()

    def _refresh_camera_selector_dropdown(self) -> None:
        combo = getattr(self, "_cam_select_combo", None)
        if combo is None:
            return
        self._cam_select_syncing = True
        try:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Default Camera", "default")
            if self._scene_camera_entries:
                ent = self._scene_camera_entries[0] or {}
                owner = str(ent.get("owner") or "").strip()
                if owner:
                    label = str(ent.get("label") or owner).strip() or owner
                    combo.addItem(label, owner)
            target = str(getattr(self, "_camera_select_mode", "default") or "default").strip() or "default"
            idx = 0
            for i in range(combo.count()):
                val = str(combo.itemData(i) or "").strip() or "default"
                if val == target:
                    idx = i
                    break
            combo.setCurrentIndex(idx)
            combo.setEnabled(combo.count() > 0)
        except Exception:
            pass
        finally:
            try:
                combo.blockSignals(False)
            except Exception:
                pass
            self._cam_select_syncing = False
        self._update_camera_selector_lock_button()

    def _build_scene_camera_pose(self, owner: str):
        if np is None:
            return None
        renderer = getattr(self, "_mgl_renderer", None) or self
        get_xf = getattr(renderer, "_mgl_get_scene_asset_xform", None)
        if not callable(get_xf):
            return None
        try:
            xf = get_xf(owner) or {}
        except Exception:
            return None
        if not isinstance(xf, dict):
            return None
        try:
            pos = np.array(
                [
                    float((xf.get("pos") or (0.0, 0.0, 0.0))[0]),
                    float((xf.get("pos") or (0.0, 0.0, 0.0))[1]),
                    float((xf.get("pos") or (0.0, 0.0, 0.0))[2]),
                ],
                dtype=np.float32,
            )
        except Exception:
            pos = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        try:
            rx = float((xf.get("rot") or (0.0, 0.0, 0.0))[0])
            ry = float((xf.get("rot") or (0.0, 0.0, 0.0))[1])
            rz = float((xf.get("rot") or (0.0, 0.0, 0.0))[2])
        except Exception:
            rx = ry = rz = 0.0

        # Match scene-model rotation convention from _mgl_set_scene_asset_xform.
        rot_rx = -float(rx)
        rot_ry = -float(ry)
        rot_rz = -float(rz)

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

        rmat = _rx(rot_rx) @ _ry(rot_ry) @ _rz(rot_rz)
        fwd = (np.array([0.0, 0.0, -1.0, 0.0], dtype=np.float32) @ rmat)[:3]
        up = (np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32) @ rmat)[:3]
        try:
            fn = float(np.linalg.norm(fwd))
            if fn > 1e-6:
                fwd = fwd / fn
            else:
                fwd = np.array([0.0, 0.0, -1.0], dtype=np.float32)
        except Exception:
            fwd = np.array([0.0, 0.0, -1.0], dtype=np.float32)
        try:
            un = float(np.linalg.norm(up))
            if un > 1e-6:
                up = up / un
            else:
                up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        except Exception:
            up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        return pos, fwd, up

    def _sync_selected_scene_camera_view(self, owner: str | None = None) -> bool:
        owner_key = str(owner or getattr(self, "_camera_select_mode", "default") or "").strip()
        if not owner_key or owner_key.lower() == "default":
            return False
        if not bool(self._camera_selector_sync_fps_from_owner_pose(owner_key)):
            return False
        try:
            if bool(getattr(self, "_fly_mode_enabled", False)):
                self._fps_camera_active = True
            else:
                self._fps_cam_sync_orbit_from_camera()
                self._fps_camera_active = False
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass
        return True

    def _select_default_camera(self) -> None:
        self._camera_select_mode = "default"
        self._camera_select_lock_enabled = False
        try:
            win = self.window()
            ctl = getattr(win, "_timeline_controller", None) if win is not None else None
            sync_ctx = getattr(ctl, "sync_timeline_context", None)
            if callable(sync_ctx):
                sync_ctx()
            else:
                set_ctx = getattr(self, "set_timeline_scene_context", None)
                if callable(set_ctx):
                    set_ctx(owner_name=None)
        except Exception:
            pass
        state = getattr(self, "_camera_select_saved_default_state", None)
        if isinstance(state, dict):
            try:
                renderer = getattr(self, "_mgl_renderer", None) or self
                apply_state = getattr(renderer, "_mgl_apply_camera_state", None)
                if callable(apply_state):
                    apply_state(dict(state))
            except Exception:
                pass
        self._camera_select_saved_default_state = None
        try:
            if not bool(getattr(self, "_fly_mode_enabled", False)):
                self._fps_camera_active = False
                self._orbit_cam_enabled = True
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass
        self._refresh_camera_selector_dropdown()

    def _select_scene_camera(self, owner: str) -> None:
        owner_key = str(owner or "").strip()
        if not owner_key:
            self._select_default_camera()
            return
        if np is None:
            return
        renderer = getattr(self, "_mgl_renderer", None) or self
        if self._camera_select_saved_default_state is None:
            try:
                get_state = getattr(renderer, "_mgl_get_camera_state", None)
                if callable(get_state):
                    saved = get_state() or {}
                    if isinstance(saved, dict):
                        saved = dict(saved)
                        saved.pop("scene_xforms", None)
                        self._camera_select_saved_default_state = saved
            except Exception:
                pass
        # Apply the selected camera pose immediately (no timeline scrub required)
        # without mutating unrelated owner tracks.
        try:
            frame = int(self._timeline_current_frame())
        except Exception:
            frame = 0
        try:
            target_owner = str(getattr(self, "_timeline_owner_name", "") or "").strip()
        except Exception:
            target_owner = ""
        try:
            if target_owner and target_owner.lower() == owner_key.lower():
                self._timeline_apply_frame_if_keyed(frame, force=False)
            else:
                keys_map = None
                list_paths = getattr(self, "_timeline_owner_file_paths", None)
                read_keys = getattr(self, "_timeline_read_owner_keys_file", None)
                if callable(list_paths) and callable(read_keys):
                    for path in list_paths() or []:
                        try:
                            file_owner, file_keys = read_keys(path)
                        except Exception:
                            continue
                        if str(file_owner or "").strip().lower() != owner_key.lower():
                            continue
                        if isinstance(file_keys, dict):
                            keys_map = file_keys
                        break
                if isinstance(keys_map, dict) and keys_map:
                    xyz_eval, rxyz_eval = self._timeline_eval_frame_values_for_owner_keys(
                        owner_key,
                        keys_map,
                        frame,
                    )
                    if not (xyz_eval is None and rxyz_eval is None):
                        if xyz_eval is None:
                            xyz_eval = self._timeline_owner_current_xyz(owner_key)
                        if xyz_eval is not None:
                            self._timeline_apply_owner_xyz_only(owner_key, xyz_eval, rxyz=rxyz_eval)
        except Exception:
            pass

        if not bool(self._sync_selected_scene_camera_view(owner_key)):
            self._select_default_camera()
            return

        try:
            set_ctx = getattr(self, "set_timeline_scene_context", None)
            if callable(set_ctx):
                scene_name = str(getattr(self, "_timeline_scene_name", "") or "").strip() or None
                project_path = None
                try:
                    win = self.window()
                    raw_path = str(getattr(win, "_current_path", "") or "").strip() if win is not None else ""
                    if raw_path:
                        project_path = raw_path
                except Exception:
                    project_path = None
                set_ctx(scene_name=scene_name, project_path=project_path, owner_name=owner_key)
        except Exception:
            pass

        self._camera_select_mode = owner_key
        try:
            self.update()
        except Exception:
            pass
        self._refresh_camera_selector_dropdown()

    def _on_camera_selector_changed(self, index: int) -> None:
        if bool(getattr(self, "_cam_select_syncing", False)):
            return
        combo = getattr(self, "_cam_select_combo", None)
        if combo is None:
            return
        try:
            val = str(combo.itemData(index) or "").strip()
        except Exception:
            val = ""
        if not val or val == "default":
            self._select_default_camera()
            return
        self._select_scene_camera(val)
        # Selecting a scene camera should enter fly navigation immediately.
        try:
            mode = str(getattr(self, "_camera_select_mode", "default") or "default").strip().lower()
            if mode != "default" and not bool(getattr(self, "_fly_mode_enabled", False)):
                self._on_fly_mode_toggled(True)
        except Exception:
            pass

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
            self._fps_nav_active = False
            self._fps_nav_last_t = time.perf_counter()
            self._fps_nav_keys = set()
            self._fps_nav_look_last_pos = None
            try:
                synced = bool(self._camera_selector_sync_fps_from_locked_owner())
                if not synced:
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
            "_mgl_scene_pivot_local_by_owner",
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
        try:
            self._set_scene_camera_options([])
        except Exception:
            pass

    def load_scene_assets(self, assets: List[Dict[str, str]], frame: bool = True) -> None:
        if not assets:
            try:
                self._set_scene_camera_options([])
            except Exception:
                pass
            return
        if self._use_moderngl:
            try:
                cam_entries: List[Dict[str, object]] = []
                for entry in assets or []:
                    if not isinstance(entry, dict):
                        continue
                    kind = str(entry.get("kind") or "").strip().lower()
                    ext = str(entry.get("ext") or "").strip().lower()
                    if kind != "camera" and ext != ".camera":
                        continue
                    owner = str(entry.get("node") or "").strip()
                    if not owner:
                        path_str = str(entry.get("path", "") or "").strip()
                        if path_str:
                            owner = Path(path_str).name
                    if not owner:
                        owner = "camera"
                    fov = None
                    try:
                        if entry.get("fov", None) is not None:
                            fov = float(entry.get("fov"))
                    except Exception:
                        fov = None
                    aspect_width = None
                    aspect_height = None
                    try:
                        aw = entry.get("aspect_width", None)
                        if aw is not None:
                            aspect_width = int(float(aw))
                    except Exception:
                        aspect_width = None
                    try:
                        ah = entry.get("aspect_height", None)
                        if ah is not None:
                            aspect_height = int(float(ah))
                    except Exception:
                        aspect_height = None
                    if aspect_width is not None and aspect_width <= 0:
                        aspect_width = None
                    if aspect_height is not None and aspect_height <= 0:
                        aspect_height = None
                    cam_entries.append(
                        {
                            "owner": owner,
                            "label": owner,
                            "fov": fov,
                            "aspect_width": aspect_width,
                            "aspect_height": aspect_height,
                        }
                    )
                if cam_entries:
                    self._set_scene_camera_options(cam_entries)
                elif bool(frame):
                    self._set_scene_camera_options([])
                else:
                    # During frame-refresh scene swaps, camera metadata can be
                    # omitted transiently; keep current dropdown options stable.
                    self._refresh_camera_selector_dropdown()
            except Exception:
                pass
            def _owner_name(entry: dict) -> str:
                name = (entry.get("node") or "").strip()
                if name:
                    return name
                path_str = str(entry.get("path", "") or "").strip()
                if path_str:
                    try:
                        return Path(path_str).name
                    except Exception:
                        return ""
                return ""

            def _owner_ext(entry: dict) -> str:
                ext = str(entry.get("ext") or "").strip().lower()
                if ext:
                    return ext
                try:
                    return Path(str(entry.get("path", "") or "")).suffix.lower()
                except Exception:
                    return ""

            def _owner_is_splat_asset(entry: dict) -> bool:
                ext = _owner_ext(entry)
                if ext == ".ply":
                    return True
                proxy = entry.get("render_proxy") if isinstance(entry, dict) else None
                if not isinstance(proxy, dict):
                    return False
                proxy_type = str(proxy.get("type") or "").strip().lower()
                return proxy_type in {"skinned_splat", "skinned_gaussian_splat"}

            def _dict_lookup_casefold(d, key: str):
                if not isinstance(d, dict):
                    return None
                if key in d:
                    return d.get(key)
                lk = str(key or "").strip().lower()
                for k, v in d.items():
                    try:
                        if str(k).strip().lower() == lk:
                            return v
                    except Exception:
                        continue
                return None

            def _dict_has_casefold(d, key: str) -> bool:
                if not isinstance(d, dict):
                    return False
                if key in d:
                    return True
                lk = str(key or "").strip().lower()
                for k in d.keys():
                    try:
                        if str(k).strip().lower() == lk:
                            return True
                    except Exception:
                        continue
                return False

            def _xf_is_identity(xf) -> bool:
                if not isinstance(xf, dict):
                    return True
                try:
                    pos = xf.get("pos", (0.0, 0.0, 0.0))
                    rot = xf.get("rot", (0.0, 0.0, 0.0))
                    scl = xf.get("scl", (1.0, 1.0, 1.0))
                    return (
                        all(abs(float(v)) < 1e-6 for v in (pos or (0.0, 0.0, 0.0)))
                        and all(abs(float(v)) < 1e-6 for v in (rot or (0.0, 0.0, 0.0)))
                        and all(abs(float(v) - 1.0) < 1e-6 for v in (scl or (1.0, 1.0, 1.0)))
                    )
                except Exception:
                    return False

            try:
                prev_mesh_xforms = dict(getattr(self, "_mgl_scene_xforms_by_owner", None) or {})
            except Exception:
                prev_mesh_xforms = {}
            try:
                prev_splat_xforms = dict(getattr(self, "_mgl_scene_splat_xforms_by_owner", None) or {})
            except Exception:
                prev_splat_xforms = {}
            seeded_mesh_xforms = {}
            seeded_splat_xforms = {}
            owner_kind_map = {}
            splat_zero_pivot_map = {}
            try:
                self._mgl_scene_xform_offset_by_owner = {}
            except Exception:
                pass
            try:
                self._mgl_scene_pivot_local_by_owner = {}
            except Exception:
                pass
            # Reset visibility map from current assets (avoid persisting prior hides)
            try:
                vis_map = {}
                for entry in assets or []:
                    if not isinstance(entry, dict):
                        continue
                    name = _owner_name(entry)
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
                    name = _owner_name(entry)
                    if not name:
                        continue
                    ext = _owner_ext(entry)
                    owner_kind_map[name] = bool(_owner_is_splat_asset(entry))
                    if ext == ".ply":
                        splat_zero_pivot_map[name] = bool(entry.get("splat_zero_pivot", False))
                    if not entry.get("xform_offset"):
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
                    name = _owner_name(entry)
                    if not name:
                        continue
                    xf = entry.get("xform")
                    if not isinstance(xf, dict):
                        continue
                    is_splat = bool(owner_kind_map.get(name, False))
                    if entry.get("xform_offset") and _xf_is_identity(xf):
                        try:
                            prev_raw = (
                                _dict_lookup_casefold(prev_splat_xforms, name)
                                if is_splat
                                else _dict_lookup_casefold(prev_mesh_xforms, name)
                            )
                            if isinstance(prev_raw, dict) and not _xf_is_identity(prev_raw):
                                seeded_prev = dict(prev_raw)
                                if is_splat and bool(splat_zero_pivot_map.get(name, False)):
                                    seeded_prev["pos"] = (0.0, 0.0, 0.0)
                                if is_splat:
                                    seeded_splat_xforms[name] = seeded_prev
                                else:
                                    seeded_mesh_xforms[name] = seeded_prev
                        except Exception:
                            pass
                        continue
                    if is_splat:
                        seeded_splat_xforms[name] = dict(xf)
                    else:
                        seeded_mesh_xforms[name] = dict(xf)

                for name, is_splat in owner_kind_map.items():
                    if is_splat:
                        if _dict_has_casefold(seeded_splat_xforms, name):
                            continue
                        prev_raw = _dict_lookup_casefold(prev_splat_xforms, name)
                        if isinstance(prev_raw, dict):
                            seeded_splat_xforms[name] = dict(prev_raw)
                    else:
                        if _dict_has_casefold(seeded_mesh_xforms, name):
                            continue
                        prev_raw = _dict_lookup_casefold(prev_mesh_xforms, name)
                        if isinstance(prev_raw, dict):
                            seeded_mesh_xforms[name] = dict(prev_raw)
                self._mgl_scene_xforms_by_owner = seeded_mesh_xforms
                self._mgl_scene_splat_xforms_by_owner = seeded_splat_xforms
            except Exception:
                pass
            try:
                self._mgl_load_scene_assets(assets, frame=frame)
            except Exception:
                pass
            # Final pass: enforce per-owner xforms after scene load (ply_sequence frame swaps).
            try:
                renderer = getattr(self, "_mgl_renderer", None) or self
                setf = getattr(renderer, "_mgl_set_scene_asset_xform", None)
                rebuild_splats = getattr(renderer, "_mgl_rebuild_scene_splats", None)
                if callable(setf):
                    applied = 0
                    touched_splats = False
                    for name, is_splat in owner_kind_map.items():
                        xf_raw = (
                            _dict_lookup_casefold(self._mgl_scene_splat_xforms_by_owner, name)
                            if is_splat
                            else _dict_lookup_casefold(self._mgl_scene_xforms_by_owner, name)
                        )
                        if not isinstance(xf_raw, dict):
                            continue
                        try:
                            setf(
                                name,
                                pos=xf_raw.get("pos"),
                                rot=xf_raw.get("rot"),
                                scl=xf_raw.get("scl"),
                                apply_to_scene_models=not bool(is_splat),
                                use_splat_xform=bool(is_splat),
                            )
                            applied += 1
                            if is_splat:
                                touched_splats = True
                        except Exception:
                            continue
                    if touched_splats and callable(rebuild_splats):
                        try:
                            rebuild_splats(preserve_camera=True)
                        except Exception:
                            pass
                    try:
                        self._mgl_log("scene: post-load xform enforce applied=" + str(applied))
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                selected = str(getattr(self, "_camera_select_mode", "default") or "default").strip()
                if selected and selected != "default":
                    self._select_scene_camera(selected)
            except Exception:
                pass
            # Keep selection/gizmo stable across frame-refresh scene swaps (ply_sequence playback).
            if bool(frame):
                # Full scene load: clear selection/gizmo to avoid stale state.
                self._xform_gizmo_owner = None
                self._xform_gizmo_owner_kind = None
                self._xform_gizmo_pos_locked = False
                self._xform_gizmo_pos = (0.0, 0.0, 0.0)
                try:
                    w = self.window()
                    if w is not None and hasattr(w, "clear_scene_asset_selection"):
                        w.clear_scene_asset_selection()
                except Exception:
                    pass
            else:
                # Timeline refresh: preserve selected owner when still present and refresh gizmo world position.
                try:
                    sel_owner = str(getattr(self, "_xform_gizmo_owner", "") or "").strip()
                    if sel_owner and _dict_has_casefold(owner_kind_map, sel_owner):
                        renderer = getattr(self, "_mgl_renderer", None) or self
                        is_splat = bool(_dict_lookup_casefold(owner_kind_map, sel_owner))
                        getf = (
                            getattr(renderer, "_mgl_get_scene_splat_xform", None)
                            if is_splat
                            else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                        )
                        xf = getf(sel_owner) if callable(getf) else {}
                        try:
                            xf_pos = tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))
                            pos = (
                                float(xf_pos[0]),
                                float(xf_pos[1]),
                                float(xf_pos[2]),
                            )
                        except Exception:
                            pos = (0.0, 0.0, 0.0)
                        if is_splat:
                            pivot = None
                            try:
                                bounds_map = (
                                    getattr(renderer, "_mgl_scene_splats_bounds_local", None)
                                    or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
                                )
                                mins = maxs = None
                                b = _dict_lookup_casefold(bounds_map, sel_owner)
                                if isinstance(b, (list, tuple)) and len(b) >= 2:
                                    mins, maxs = b[0], b[1]
                                pivot_fn = getattr(renderer, "_mgl_owner_pivot_local", None)
                                if callable(pivot_fn):
                                    raw = (
                                        pivot_fn(sel_owner, mins, maxs)
                                        if (mins is not None and maxs is not None)
                                        else pivot_fn(sel_owner)
                                    )
                                    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
                                        pivot = (
                                            float(raw[0]),
                                            float(raw[1]),
                                            float(raw[2]),
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
                                    float(pos[0] + pivot[0]),
                                    float(pos[1] + pivot[1]),
                                    float(pos[2] + pivot[2]),
                                )
                        self._xform_gizmo_owner_kind = "splat" if is_splat else "mesh"
                        self._xform_gizmo_pos_locked = False
                        self._xform_gizmo_pos = pos
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
        old_lk = old_key.lower()

        def _dict_contains_casefold(d, key: str) -> bool:
            if not isinstance(d, dict):
                return False
            if key in d:
                return True
            lk = str(key or "").strip().lower()
            for k in d.keys():
                try:
                    if str(k).strip().lower() == lk:
                        return True
                except Exception:
                    continue
            return False

        def _dict_rename_casefold(d, src: str, dst: str) -> bool:
            if not isinstance(d, dict):
                return False
            if src in d:
                try:
                    d[dst] = d.pop(src)
                    return True
                except Exception:
                    return False
            lk = str(src or "").strip().lower()
            for k in list(d.keys()):
                try:
                    if str(k).strip().lower() != lk:
                        continue
                    d[dst] = d.pop(k)
                    return True
                except Exception:
                    continue
            return False

        # track whether this owner actually had splats
        had_splat = False
        try:
            had_splat = _dict_contains_casefold(getattr(self, "_mgl_scene_splats", None), old_key)
        except Exception:
            had_splat = False

        # rename visibility entry
        try:
            _dict_rename_casefold(getattr(self, "_mgl_scene_visibility", None), old_key, new_key)
        except Exception:
            pass

        # preserve per-owner transforms when renaming
        for attr in (
            "_mgl_scene_xforms_by_owner",
            "_mgl_scene_splat_xforms_by_owner",
            "_mgl_scene_xform_offset_by_owner",
        ):
            try:
                d = getattr(self, attr, None)
                _dict_rename_casefold(d, old_key, new_key)
            except Exception:
                pass

        # rename splat storage only if needed
        if had_splat:
            try:
                _dict_rename_casefold(getattr(self, "_mgl_scene_splats", None), old_key, new_key)
            except Exception:
                pass

        # Keep camera selector entries synchronized with owner renames.
        changed_selector = False
        try:
            entries = getattr(self, "_scene_camera_entries", None)
            if isinstance(entries, list):
                for ent in entries:
                    if not isinstance(ent, dict):
                        continue
                    owner = str(ent.get("owner") or "").strip()
                    if owner and owner.lower() == old_lk:
                        ent["owner"] = new_key
                        ent["label"] = new_key
                        changed_selector = True
        except Exception:
            pass
        try:
            fmap = getattr(self, "_scene_camera_fov_by_owner", None)
            if _dict_rename_casefold(fmap, old_key, new_key):
                changed_selector = True
        except Exception:
            pass
        try:
            mode = str(getattr(self, "_camera_select_mode", "default") or "default").strip()
            if mode and mode.lower() == old_lk:
                self._camera_select_mode = new_key
                changed_selector = True
        except Exception:
            pass
        if changed_selector:
            try:
                self._refresh_camera_selector_dropdown()
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
            "3D Models (*.obj *.gltf *.glb *.fbx *.bvh);;All Files (*.*)",
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
            "3D Models (*.obj *.gltf *.glb *.fbx *.bvh);;All Files (*.*)",
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

                try:
                    rot, _rot_is_splat = self._get_owner_rot_deg(owner)
                except Exception:
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
        try:
            is_pose_joint = getattr(renderer, "_mgl_retarget_owner_is_target_pose_joint", None)
            get_pose_rot = getattr(renderer, "_mgl_retarget_get_target_pose_joint_rotation", None)
            if callable(is_pose_joint) and callable(get_pose_rot) and bool(is_pose_joint(owner)):
                rot = get_pose_rot(owner)
                return (float(rot[0]), float(rot[1]), float(rot[2])), False
        except Exception:
            pass
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
            try:
                is_pose_joint = getattr(renderer, "_mgl_retarget_owner_is_target_pose_joint", None)
                set_pose_rot = getattr(renderer, "_mgl_retarget_set_target_pose_joint_rotation", None)
                if callable(is_pose_joint) and callable(set_pose_rot) and bool(is_pose_joint(owner)):
                    set_pose_rot(
                        owner,
                        (float(rot_deg[0]), float(rot_deg[1]), float(rot_deg[2])),
                        notify_scene=False,
                    )
                    return
            except Exception:
                pass
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
        controls_h = float(self._bottom_overlay_height() or 0.0)
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
        controls_h = float(self._bottom_overlay_height() or 0.0)
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
            wire_seq = hotkeys_config.keyseq("gl_wireframe_toggle", "H")
            grid_seq = hotkeys_config.keyseq("gl_grid_toggle", "G")
        except Exception:
            wire_seq = "H"
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
        if key in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            if self._timeline_delete_selected_keys():
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

from echograph.ui.gl_view_mouse import (
    install_graph_gl_view_mouse_methods as _install_graph_gl_view_mouse_methods,
)
_install_graph_gl_view_mouse_methods(GraphGLView)
del _install_graph_gl_view_mouse_methods

