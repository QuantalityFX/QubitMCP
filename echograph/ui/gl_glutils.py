# echograph/ui/gl_glutils.py
from __future__ import annotations

from typing import Dict, Optional, Tuple


def build_qt_program(
    QOpenGLShaderProgram,
    QOpenGLShader,
    vertex_src: str,
    fragment_src: str,
    bind_locations: Optional[Dict[str, int]] = None,
) -> Tuple[object, str]:
    """
    Returns (program, error_message). error_message == "" on success.
    """
    if QOpenGLShaderProgram is None or QOpenGLShader is None:
        return None, "Shaders unavailable"

    program = QOpenGLShaderProgram()

    if not program.addShaderFromSourceCode(QOpenGLShader.Vertex, vertex_src):
        return None, program.log().strip() or "Vertex shader failed"

    if not program.addShaderFromSourceCode(QOpenGLShader.Fragment, fragment_src):
        return None, program.log().strip() or "Fragment shader failed"

    if bind_locations:
        for name, loc in bind_locations.items():
            try:
                program.bindAttributeLocation(name, int(loc))
            except Exception:
                pass

    if not program.link():
        return None, program.log().strip() or "Shader link failed"

    return program, ""


def upload_scene_texture(
    QtGui,
    QOpenGLTexture,
    image,
    prev_texture,
):
    """
    Returns (new_texture, mipmaps_enabled).
    """
    if image is None:
        return prev_texture, False
    if QOpenGLTexture is None:
        return prev_texture, False

    if prev_texture is not None:
        try:
            prev_texture.destroy()
        except Exception:
            pass

    if hasattr(QtGui.QImage, "Format_RGBA8888"):
        image = image.convertToFormat(QtGui.QImage.Format_RGBA8888)
    else:
        image = image.convertToFormat(QtGui.QImage.Format_ARGB32)

    image = image.mirrored()

    tex = QOpenGLTexture(image)
    tex.setMagnificationFilter(QOpenGLTexture.Linear)
    try:
        tex.setWrapMode(QOpenGLTexture.ClampToEdge)
    except Exception:
        pass

    mipmaps_enabled = False
    try:
        if hasattr(tex, "setAutoMipMapGenerationEnabled"):
            tex.setAutoMipMapGenerationEnabled(True)
        if hasattr(tex, "generateMipMaps"):
            tex.generateMipMaps()

        mip_levels = None
        if hasattr(tex, "mipLevels"):
            mip_levels = int(tex.mipLevels())

        if hasattr(tex, "hasMipMaps"):
            mipmaps_enabled = bool(tex.hasMipMaps())
        elif mip_levels is not None:
            mipmaps_enabled = mip_levels > 1
    except Exception:
        mipmaps_enabled = False

    if mipmaps_enabled and hasattr(QOpenGLTexture, "LinearMipMapLinear"):
        tex.setMinificationFilter(QOpenGLTexture.LinearMipMapLinear)
    else:
        tex.setMinificationFilter(QOpenGLTexture.Linear)

    return tex, mipmaps_enabled


def update_quad_vbo(QtCore, struct, quad_vbo, scene_src_rect):
    """
    Returns (quad_ready: bool).
    """
    if quad_vbo is None or scene_src_rect is None:
        return False

    rect = scene_src_rect
    x0, y0 = rect.left(), rect.top()
    x1, y1 = rect.right(), rect.bottom()
    extent = max(abs(x1 - x0), abs(y1 - y0), 1.0)
    plane_z = -max(10.0, extent * 0.05)

    verts = [
        x0, y0, plane_z, 0.0, 1.0,
        x1, y0, plane_z, 1.0, 1.0,
        x0, y1, plane_z, 0.0, 0.0,
        x1, y1, plane_z, 1.0, 0.0,
    ]

    data = QtCore.QByteArray(struct.pack(f"{len(verts)}f", *verts))
    if quad_vbo.bind():
        quad_vbo.allocate(data, data.size())
        quad_vbo.release()
        return True

    return False


def float_bytes(QtCore, array, values):
    buf = array("f", values).tobytes()
    return QtCore.QByteArray(buf)