# echograph/ui/gl_view_example.py
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from PySide6 import QtCore, QtGui
    from PySide6.QtOpenGL import (
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLBuffer,
        QOpenGLVertexArrayObject,
    )
except Exception:
    from PySide2 import QtCore, QtGui  # type: ignore
    from PySide2.QtOpenGL import (  # type: ignore
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLBuffer,
        QOpenGLVertexArrayObject,
    )

# Keep local GL enums to avoid coupling with gl_view.py
GL_DEPTH_TEST = 0x0B71


def example_cube_data(view: Any) -> Tuple[List[float], List[int]]:
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


def example_grid_data(view: Any, extent: float = 12.0, step: float = 1.0) -> List[float]:
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


def upload_example_grid(view: Any, vertices: List[float]) -> bool:
    if QOpenGLBuffer is None or not vertices:
        return False
    if getattr(view, "_example_grid_vbo", None) is None:
        view._example_grid_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        view._example_grid_vbo.create()
    if not view._example_grid_vbo.bind():
        return False
    try:
        data = view._float_bytes(vertices)
    except Exception:
        view._example_grid_vbo.release()
        return False
    view._example_grid_vbo.allocate(data, data.size())
    view._example_grid_vbo.release()
    view._example_grid_count = len(vertices) // 7
    return True


def build_example_program(
    view: Any,
    vertex_src: str,
    fragment_src: str,
    bind_locations: Optional[Dict[str, int]] = None,
) -> Optional[QtGui.QOpenGLShaderProgram]:
    # Try local name first, then fall back to gl_view module.
    fn = globals().get("build_qt_program", None)
    if fn is None:
        try:
            from echograph.ui import gl_view as _gl_view_mod
            fn = getattr(_gl_view_mod, "build_qt_program", None)
        except Exception:
            fn = None

    if fn is None:
        view._shader_error = "build_qt_program unavailable"
        return None

    program, err = fn(
        QOpenGLShaderProgram,
        QOpenGLShader,
        vertex_src,
        fragment_src,
        bind_locations,
    )
    if program is None:
        view._shader_error = err
        return None
    return program


def upload_example_vertices(view: Any, vertices: List[float]) -> bool:
    if QOpenGLBuffer is None:
        view._shader_error = "OpenGL buffers unavailable"
        return False
    if not vertices:
        view._shader_error = "No vertices to upload"
        return False

    if getattr(view, "_example_vbo", None) is None:
        view._example_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        view._example_vbo.create()

    if not view._example_vbo.bind():
        view._shader_error = "Vertex buffer bind failed"
        return False

    try:
        data = view._float_bytes(vertices)
    except Exception:
        view._shader_error = "Vertex buffer build failed"
        view._example_vbo.release()
        return False

    view._example_vbo.allocate(data, data.size())
    view._example_vbo.release()
    view._example_draw_count = len(vertices) // 7
    view._example_index_count = 0
    return True

def init_example_pipeline(view: Any) -> None:
    if getattr(view, "_gl", None) is None:
        view._shader_error = "GL functions unavailable"
        return

    if QOpenGLShaderProgram is None or QOpenGLShader is None:
        view._shader_error = "Shaders unavailable"
        return

    try:
        view._gl.glEnable(GL_DEPTH_TEST)
        view._gl.glClearColor(0.8, 0.8, 0.8, 1.0)
    except Exception:
        view._shader_error = "OpenGL state init failed"
        return

    # VAO for the example pipeline (optional)
    if QOpenGLVertexArrayObject is not None:
        view._example_vao = QOpenGLVertexArrayObject()
        try:
            view._example_vao.create()
        except Exception:
            view._example_vao = None

    # Pull shaders from wherever gl_view.py currently stores them
    shaders = getattr(view, "SHADERS", None)
    if shaders is None:
        shaders = getattr(view.__class__, "SHADERS", None)

    if shaders is None:
        try:
            import echograph.ui.gl_view as _gl_view_mod
            shaders = getattr(_gl_view_mod, "SHADERS", None)
        except Exception:
            shaders = None

    if shaders is None:
        view._shader_error = "Example shaders missing (SHADERS not found)"
        return

    view._shader_error = ""

    vertex_330 = shaders["example_vertex_330"]
    fragment_330 = shaders["example_fragment_330"]
    vertex_legacy = shaders["example_vertex_legacy"]
    fragment_legacy = shaders["example_fragment_legacy"]

    bind_locations = {"a_position": 0, "a_color": 1}

    program = view._build_example_program(vertex_330, fragment_330, bind_locations)
    if program is None:
        program = view._build_example_program(vertex_legacy, fragment_legacy, bind_locations)

    if program is None:
        if not view._shader_error:
            view._shader_error = "Shader compile failed"
        return

    view._shader_error = ""
    view._example_program = program

    # Upload initial cube + grid
    vertices, _ = view._example_cube_data()
    if not view._upload_example_vertices(vertices):
        return

    grid_vertices = view._example_grid_data(view._example_grid_extent, 1.0)
    view._upload_example_grid(grid_vertices)

    # Defaults + transforms
    view._example_model_center = (0.0, 0.0, 0.0)
    view._example_model_base_scale = 1.0
    view._example_model_scale = 1.0
    view._update_example_transform()
    view._update_example_projection()

def queue_example_model(view: Any, path: Path) -> None:
    # Try to use whatever gl_view.py uses, without guessing module paths.
    loader = getattr(view, "load_model", None)
    if not callable(loader):
        loader = getattr(view, "_load_model", None)

    if not callable(loader):
        try:
            # gl_view.py already has a working load_model in its module scope
            from echograph.ui import gl_view as _gl_view_mod
            loader = getattr(_gl_view_mod, "load_model", None)
        except Exception:
            loader = None

    if not callable(loader):
        view._shader_error = "Model loader unavailable (load_model)"
        return

    try:
        model_data = loader(path)
    except Exception:
        model_data = None

    if not model_data or not getattr(model_data, "vertices", None):
        view._shader_error = "Model load failed"
        return

    color = (0.56, 0.86, 0.98, 1.0)
    vertices: List[float] = []
    src = model_data.vertices
    for i in range(0, len(src), 3):
        vertices.extend([src[i], src[i + 1], src[i + 2], color[0], color[1], color[2], color[3]])

    view._example_pending_vertices = vertices
    view._example_pending_bounds = getattr(model_data, "bounds", None)
    view._example_model_path = str(path)
    view._example_pending_count = len(vertices) // 7
    view.update()


def apply_example_bounds(view: Any, bounds: Tuple[float, float, float, float, float, float]) -> None:
    min_x, min_y, min_z, max_x, max_y, max_z = bounds
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    cz = (min_z + max_z) * 0.5
    extent = max(max_x - min_x, max_y - min_y, max_z - min_z, 1.0)

    view._example_model_center = (cx, cy, cz)
    view._example_model_base_scale = 18.0 / extent
    view._example_model_extent = extent

    scaled_extent = view._example_scaled_extent()
    view._example_grid_extent = max(6.0, scaled_extent * 1.2)
    step = max(0.5, view._example_grid_extent / 20.0)

    view._upload_example_grid(view._example_grid_data(view._example_grid_extent, step))
    view._update_example_transform()
    view._update_example_projection()

def example_scaled_extent(view: Any) -> float:
    extent = max(1.0, float(view._example_model_extent))
    scale = max(1e-4, float(view._example_model_base_scale * view._example_model_scale))
    return max(0.1, extent * scale)

def update_example_transform(view: Any) -> None:
    if not view._use_example_pipeline:
        return

    cx, cy, cz = view._example_model_center
    scale = view._example_model_base_scale * view._example_model_scale

    view._example_transform.setToIdentity()
    view._example_transform.scale(scale, scale, scale)
    view._example_transform.translate(-cx, -cy, -cz)

    view._update_example_projection()

def frame_example_camera(view: Any) -> None:
    center = QtGui.QVector3D(*view._example_model_center)
    extent = view._example_scaled_extent()

    direction = QtGui.QVector3D(1.0, 1.0, 1.0)
    direction.normalize()

    distance = max(12.0, extent * 2.5)
    view._example_cam_look = center
    view._example_cam_pos = center + direction * distance

    view._update_example_projection()


def apply_example_pending(view: Any) -> None:
    if view._example_pending_vertices is None:
        return
    vertices = view._example_pending_vertices
    if not view._upload_example_vertices(vertices):
        return
    bounds = view._example_pending_bounds
    if bounds is not None:
        view._apply_example_bounds(bounds)
        view._frame_example_camera()
    view._example_pending_vertices = None
    view._example_pending_bounds = None
    view._example_pending_count = 0

def update_example_projection(view: Any) -> None:
    w = max(1, view.width())
    h = max(1, view.height())

    distance = (view._example_cam_pos - view._example_cam_look).length()
    extent = view._example_scaled_extent()

    near = max(0.02, distance - extent * 4.0)
    far = max(distance + extent * 4.0, near + extent * 8.0)

    view._example_near = near
    view._example_far = far

    view._example_proj.setToIdentity()
    view._example_proj.perspective(view._example_fov, w / float(h), view._example_near, view._example_far)

def update_example_camera_basis(view: Any) -> None:
    direction = view._example_cam_pos - view._example_cam_look
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

    view._example_cam_right = right
    view._example_cam_up = up

def sync_example_gizmo(view: Any) -> None:
    direction = view._example_cam_pos - view._example_cam_look
    if direction.lengthSquared() < 1e-6:
        return
    dist = direction.length()
    if dist <= 1e-6:
        return
    try:
        view._cam_yaw = math.atan2(direction.x(), direction.z())
        view._cam_pitch = math.asin(direction.y() / dist)
    except Exception:
        return

def example_view_matrix(view: Any) -> QtGui.QMatrix4x4:
    view._update_example_camera_basis()
    view._sync_example_gizmo()
    m = QtGui.QMatrix4x4()
    m.lookAt(view._example_cam_pos, view._example_cam_look, view._example_cam_up)
    return m

def example_orbit(view: Any, delta: QtCore.QPointF) -> None:
    if delta is None:
        return
    view._update_example_camera_basis()

    axis_up = view._example_cam_up
    axis_right = view._example_cam_right

    yaw_deg = -float(delta.x()) * view._example_rotate_speed
    pitch_deg = -float(delta.y()) * view._example_rotate_speed

    direction = view._example_cam_pos - view._example_cam_look
    if direction.lengthSquared() < 1e-6:
        return

    q_yaw = QtGui.QQuaternion.fromAxisAndAngle(axis_up, yaw_deg)
    q_pitch = QtGui.QQuaternion.fromAxisAndAngle(axis_right, pitch_deg)

    direction = q_yaw.rotatedVector(direction)
    direction = q_pitch.rotatedVector(direction)

    view._example_cam_pos = view._example_cam_look + direction


def example_pan(view: Any, delta: QtCore.QPointF) -> None:
    if delta is None:
        return
    view._update_example_camera_basis()

    distance = (view._example_cam_pos - view._example_cam_look).length()
    scale = max(0.1, distance) * view._example_pan_speed

    offset = (-float(delta.x()) * view._example_cam_right + float(delta.y()) * view._example_cam_up) * scale
    view._example_cam_pos += offset
    view._example_cam_look += offset


def example_zoom(view: Any, delta_steps: float) -> None:
    if abs(delta_steps) < 1e-6:
        return

    direction = view._example_cam_pos - view._example_cam_look
    dist = direction.length()
    if dist < 1e-6:
        return
    direction.normalize()

    base = 1.0 + 0.12 * view._example_zoom_step
    if base <= 1.0:
        base = 1.05

    zoom = math.pow(base, abs(delta_steps))
    if delta_steps > 0.0:
        dist = dist / zoom
    else:
        dist = dist * zoom

    min_dist = 0.1
    max_dist = max(min_dist * 2.0, view._example_far * 0.95)
    dist = max(min_dist, min(max_dist, dist))

    view._example_cam_pos = view._example_cam_look + direction * dist
    view._update_example_projection()


def upload_example_grid(view: Any, vertices: List[float]) -> bool:
    if QOpenGLBuffer is None:
        return False
    if not vertices:
        return False

    if getattr(view, "_example_grid_vbo", None) is None:
        view._example_grid_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        view._example_grid_vbo.create()

    if not view._example_grid_vbo.bind():
        return False

    try:
        data = view._float_bytes(vertices)
    except Exception:
        view._example_grid_vbo.release()
        return False

    view._example_grid_vbo.allocate(data, data.size())
    view._example_grid_vbo.release()
    view._example_grid_count = len(vertices) // 7
    return True
