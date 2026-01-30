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
    program, err = view.build_qt_program(
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

    if QOpenGLVertexArrayObject is not None:
        view._example_vao = QOpenGLVertexArrayObject()
        try:
            view._example_vao.create()
        except Exception:
            view._example_vao = None

    view._shader_error = ""
    shaders = view.SHADERS if hasattr(view, "SHADERS") else getattr(view, "_SHADERS", None)
    if shaders is None:
        shaders = getattr(view, "SHADERS", None)
    shaders = getattr(view, "SHADERS", None) if shaders is None else shaders

    vertex_330 = view.SHADERS["example_vertex_330"]
    fragment_330 = view.SHADERS["example_fragment_330"]
    vertex_legacy = view.SHADERS["example_vertex_legacy"]
    fragment_legacy = view.SHADERS["example_fragment_legacy"]

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

    vertices, _ = view._example_cube_data()
    if not view._upload_example_vertices(vertices):
        return
    grid_vertices = view._example_grid_data(view._example_grid_extent, 1.0)
    view._upload_example_grid(grid_vertices)

    view._example_model_center = (0.0, 0.0, 0.0)
    view._example_model_base_scale = 1.0
    view._example_model_scale = 1.0
    view._update_example_transform()
    view._update_example_projection()


def queue_example_model(view: Any, path: Path) -> None:
    try:
        model_data = view.load_model(path)
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
