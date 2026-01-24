# echograph/ui/axis_gizmo_overlay.py
from __future__ import annotations

import math
import struct
from PySide6.QtOpenGL import (
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLBuffer,
    QOpenGLVertexArrayObject,
)
from PySide6 import QtGui

# GL enums (no PyOpenGL dependency)
GL_FLOAT = 0x1406
GL_LINES = 0x0001
GL_TRIANGLES = 0x0004
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303


VERT = """
#version 330
layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_col;
uniform mat4 u_mvp;
out vec3 v_col;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_col = in_col;
}
"""

FRAG = """
#version 330
in vec3 v_col;
out vec4 fragColor;
void main() {
    fragColor = vec4(v_col, 1.0);
}
"""


class AxisGizmoOverlay:
    def __init__(self) -> None:
        self._gl = None
        self._prog: QOpenGLShaderProgram | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._vao: QOpenGLVertexArrayObject | None = None
        self._ready = False
        self._line_vert_count = 0
        self._tri_vert_count = 0

    def ensure_gl(self, gl_view) -> bool:
        if self._ready:
            return True

        ctx = gl_view.context()
        self._gl = ctx.functions()
        self._gl.initializeOpenGLFunctions()

        prog = QOpenGLShaderProgram()
        if not prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT):
            return False
        if not prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG):
            return False
        if not prog.link():
            return False
        self._prog = prog

        verts: list[float] = []

        def add_cone(tip, axis_dir, radius, height, segments, col):
            tx, ty, tz = tip
            dx, dy, dz = axis_dir
            bx = tx - dx * height
            by = ty - dy * height
            bz = tz - dz * height

            if abs(dx) < 0.9:
                hx, hy, hz = (1.0, 0.0, 0.0)
            else:
                hx, hy, hz = (0.0, 1.0, 0.0)

            ux = dy * hz - dz * hy
            uy = dz * hx - dx * hz
            uz = dx * hy - dy * hx
            ulen = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
            ux, uy, uz = ux / ulen, uy / ulen, uz / ulen

            vx = dy * uz - dz * uy
            vy = dz * ux - dx * uz
            vz = dx * uy - dy * ux

            r, g, b = col
            for i in range(segments):
                a0 = (i / segments) * (math.pi * 2.0)
                a1 = ((i + 1) / segments) * (math.pi * 2.0)

                x0 = bx + radius * (math.cos(a0) * ux + math.sin(a0) * vx)
                y0 = by + radius * (math.cos(a0) * uy + math.sin(a0) * vy)
                z0 = bz + radius * (math.cos(a0) * uz + math.sin(a0) * vz)

                x1 = bx + radius * (math.cos(a1) * ux + math.sin(a1) * vx)
                y1 = by + radius * (math.cos(a1) * uy + math.sin(a1) * vy)
                z1 = bz + radius * (math.cos(a1) * uz + math.sin(a1) * vz)

                verts.extend([tx, ty, tz, r, g, b])
                verts.extend([x0, y0, z0, r, g, b])
                verts.extend([x1, y1, z1, r, g, b])

        axis_len = 1.0
        cone_radius = 0.06
        cone_height = 0.18
        line_end = axis_len - cone_height
        segs = 12

        # lines (6 verts)
        verts.extend([0.0, 0.0, 0.0, 1.0, 0.0, 0.0])
        verts.extend([line_end, 0.0, 0.0, 1.0, 0.0, 0.0])

        verts.extend([0.0, 0.0, 0.0, 0.0, 1.0, 0.0])
        verts.extend([0.0, line_end, 0.0, 0.0, 1.0, 0.0])

        verts.extend([0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
        verts.extend([0.0, 0.0, line_end, 0.0, 0.0, 1.0])

        self._line_vert_count = 6

        # cones (triangles)
        add_cone((axis_len, 0.0, 0.0), (1.0, 0.0, 0.0), cone_radius, cone_height, segs, (1.0, 0.0, 0.0))
        add_cone((0.0, axis_len, 0.0), (0.0, 1.0, 0.0), cone_radius, cone_height, segs, (0.0, 1.0, 0.0))
        add_cone((0.0, 0.0, axis_len), (0.0, 0.0, 1.0), cone_radius, cone_height, segs, (0.0, 0.0, 1.0))

        total_verts = len(verts) // 6
        self._tri_vert_count = total_verts - self._line_vert_count

        data = struct.pack(f"{len(verts)}f", *verts)

        vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        vbo.create()
        vbo.bind()
        vbo.allocate(data, len(data))

        vao = QOpenGLVertexArrayObject()
        vao.create()
        vao.bind()

        prog.bind()
        stride = 6 * 4
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
        prog.enableAttributeArray(1)
        prog.setAttributeBuffer(1, GL_FLOAT, 3 * 4, 3, stride)

        vao.release()
        vbo.release()
        prog.release()

        self._vbo = vbo
        self._vao = vao
        self._ready = True
        return True

    def draw(self, mvp: QtGui.QMatrix4x4) -> None:
        if not self._ready or self._gl is None or self._prog is None or self._vao is None:
            return

        self._gl.glDisable(GL_DEPTH_TEST)
        self._gl.glEnable(GL_BLEND)
        self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self._gl.glLineWidth(3.0)

        self._prog.bind()
        self._prog.setUniformValue("u_mvp", mvp)

        self._vao.bind()
        self._gl.glDrawArrays(GL_LINES, 0, self._line_vert_count)
        self._gl.glDrawArrays(GL_TRIANGLES, self._line_vert_count, self._tri_vert_count)
        self._vao.release()

        self._prog.release()
