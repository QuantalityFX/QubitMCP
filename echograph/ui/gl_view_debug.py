# echograph/ui/gl_view_debug.py
from __future__ import annotations

from pathlib import Path
from typing import Any, List

try:
    from PySide6 import QtCore
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtOpenGL import QOpenGLShaderProgram, QOpenGLBuffer
    from PySide6.QtGui import QOpenGLTexture
except Exception:
    from PySide2 import QtCore  # type: ignore
    try:
        from PySide2.QtWidgets import QOpenGLWidget  # type: ignore
    except Exception:
        QOpenGLWidget = None  # type: ignore
    try:
        from PySide2.QtOpenGL import QOpenGLShaderProgram, QOpenGLBuffer  # type: ignore
    except Exception:
        QOpenGLShaderProgram = None  # type: ignore
        QOpenGLBuffer = None  # type: ignore
    try:
        from PySide2.QtGui import QOpenGLTexture  # type: ignore
    except Exception:
        QOpenGLTexture = None  # type: ignore


def debug_status_lines(view: Any, include_paths: bool = False) -> List[str]:
    w = int(view.width())
    h = int(view.height())

    lines: List[str] = ["3D View Debug"]
    lines.append(f"Viewport: {w}x{h}")
    lines.append(f"FPS: {getattr(view, '_fps', 0.0):5.1f}")
    lines.append(f"QOpenGLWidget: {'OK' if QOpenGLWidget is not None else 'missing'}")
    lines.append(f"GL context: {'OK' if hasattr(view, '_gl') else 'missing'}")

    ctx = None
    try:
        ctx = view.context()
    except Exception:
        ctx = None

    if ctx is not None:
        try:
            fmt = ctx.format()
            lines.append(f"Depth buffer: {fmt.depthBufferSize()}")
        except Exception:
            pass

    if getattr(view, "_use_moderngl", False):
        lines.append(f"Shaders: {'OK' if getattr(view, '_mgl_prog', None) is not None else 'init'}")
    else:
        if QOpenGLShaderProgram is None:
            lines.append("Shaders: unavailable")
        elif getattr(view, "_shader_error", None):
            lines.append("Shaders: error")
        elif getattr(view, "_use_example_pipeline", False) and getattr(view, "_example_program", None):
            lines.append("Shaders: OK")
        elif getattr(view, "_quad_program", None) and getattr(view, "_mesh_program", None):
            lines.append("Shaders: OK")
        else:
            lines.append("Shaders: init")

    lines.append(f"QOpenGLTexture: {'OK' if QOpenGLTexture is not None else 'missing'}")
    lines.append(f"QOpenGLBuffer: {'OK' if QOpenGLBuffer is not None else 'missing'}")
    lines.append(f"VAO: {'OK' if getattr(view, '_vao', None) is not None else 'none'}")
    lines.append(f"Mipmaps: {'on' if getattr(view, '_mipmaps_enabled', False) else 'off'}")
    lines.append(f"Render paused: {'on' if getattr(view, '_render_paused', False) else 'off'}")

    if getattr(view, "_use_moderngl", False):
        lines.append("Renderer: ModernGL")
        lines.append(f"ModernGL: {'OK' if getattr(view, '_mgl_ctx', None) is not None else 'missing'}")
        lines.append(f"Camera FOV: {getattr(view, '_mgl_fov', 0.0):.1f}")
        lines.append(f"Camera zoom: {getattr(view, '_mgl_camera_zoom', 0.0):.2f}")

        mesh_path = getattr(view, "_mgl_mesh_path", "")
        if mesh_path:
            lines.append(f"Model: {Path(mesh_path).name}")

        if getattr(view, "_mgl_texture_override", False) and getattr(view, "_mgl_texture_path", ""):
            lines.append(f"Texture: {Path(view._mgl_texture_path).name}")
        elif getattr(view, "_mgl_texture_paths", None):
            names = [Path(p).name for p in view._mgl_texture_paths if p]
            if len(names) > 3:
                shown = ", ".join(names[:3])
                lines.append(f"Textures: {shown} (+{len(names) - 3} more)")
            else:
                lines.append("Textures: " + ", ".join(names))
        else:
            lines.append("Texture: none")

        lines.append(f"Mesh indices: {getattr(view, '_mgl_mesh_vertex_count', 0)}")
        if getattr(view, "_mgl_uv_vertex_count", 0):
            lines.append(f"UV verts: {view._mgl_uv_vertex_count}")
        lines.append(f"Grid lines: {getattr(view, '_mgl_grid_vertex_count', 0)}")

        if getattr(view, "_mgl_error", ""):
            lines.append(f"ModernGL error: {view._mgl_error}")

    elif getattr(view, "_use_example_pipeline", False):
        lines.append("Example pipeline: on")
        shader_state = "error" if getattr(view, "_shader_error", None) else ("OK" if getattr(view, "_example_program", None) else "init")
        lines.append(f"Example shaders: {shader_state}")
        lines.append(f"Camera FOV: {getattr(view, '_example_fov', 0.0):.1f}")

        try:
            distance = (view._example_cam_pos - view._example_cam_look).length()
            lines.append(f"Camera dist: {distance:.2f}")
        except Exception:
            pass

        lines.append(f"Clip range: {getattr(view, '_example_near', 0.0):.2f}-{getattr(view, '_example_far', 0.0):.1f}")
        lines.append(f"Model extent: {getattr(view, '_example_model_extent', 0.0):.2f}")

        try:
            lines.append(f"Scaled extent: {view._example_scaled_extent():.2f}")
        except Exception:
            pass

        if getattr(view, "_example_model_path", ""):
            lines.append(f"Example model: {Path(view._example_model_path).name}")

        if getattr(view, "_example_vao", None) is not None:
            lines.append("Example VAO: OK")

        lines.append(f"Example verts: {getattr(view, '_example_draw_count', 0)}")

        if getattr(view, "_shader_error", None):
            lines.append(f"Example error: {view._shader_error}")

    if not getattr(view, "_render_scene_plane", False):
        lines.append("Scene tex: disabled")
    elif getattr(view, "_scene_texture", None) is not None:
        lines.append("Scene tex: ready")
    else:
        lines.append("Scene tex: none")

    if not getattr(view, "_render_scene_models", False):
        lines.append("Meshes: disabled")
    else:
        lines.append(f"Meshes: {len(getattr(view, '_meshes', {}))}")

    lines.append(f"Model scale: {getattr(view, '_model_scale_multiplier', 1.0):.2f}x")

    if getattr(view, "_show_test_cube", False):
        lines.append("Test cube: on (1x1)")

    if getattr(view, "_debug_wire", None) is not None and getattr(view, "_show_test_cube", False):
        lines.append("Cube wire: on")

    if getattr(view, "_use_moderngl", False):
        lines.append("Projection: perspective")
    elif getattr(view, "_use_example_pipeline", False):
        lines.append("Projection: perspective")
    else:
        lines.append(f"Projection: {'ortho' if getattr(view, '_use_ortho', False) else 'perspective'}")

    mesh_paths: List[str] = []
    for meta in getattr(view, "_mesh_meta", {}).values():
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

    lines.append(f"Scene content: {'blank' if getattr(view, '_scene_content_blank', False) else 'ok'}")

    if getattr(view, "_use_moderngl", False):
        pass
    elif getattr(view, "_use_example_pipeline", False):
        if getattr(view, "_example_grid_count", 0):
            lines.append(f"Example grid lines: {view._example_grid_count}")
    elif getattr(view, "_grid_count", 0):
        lines.append(f"Grid lines: {view._grid_count}")

    try:
        rect = QtCore.QRectF(getattr(view, "_scene_src_rect", QtCore.QRectF()))
        if not rect.isNull():
            lines.append(f"Scene rect: {int(rect.width())}x{int(rect.height())}")
    except Exception:
        pass

    return lines
