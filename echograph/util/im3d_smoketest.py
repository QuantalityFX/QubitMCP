# echograph/util/im3d_smoketest.py
from __future__ import annotations

import sys
import struct
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLBuffer,
    QOpenGLVertexArrayObject,
)

# GL enum constants (no PyOpenGL dependency)
GL_FLOAT = 0x1406
GL_LINES = 0x0001
GL_DEPTH_TEST = 0x0B71
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_TRIANGLES = 0x0004

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


class Smoke3D(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self._gl = None
        self._prog: QOpenGLShaderProgram | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._vao: QOpenGLVertexArrayObject | None = None

        self._t = 0.0

        self.setMinimumSize(900, 600)
        self.setWindowTitle("Qt GL Smoke Test (3D Axis)")

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)
        self._line_vert_count = 0
        self._tri_vert_count = 0

    def _tick(self):
        self._t += 0.016
        self.update()

    def initializeGL(self) -> None:
        ctx = self.context()
        self._gl = ctx.functions()
        self._gl.initializeOpenGLFunctions()

        self._prog = QOpenGLShaderProgram()
        self._prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG)
        ok = self._prog.link()
        print("[SMOKE3D] program link:", ok)

        # World-space axis: origin to +X, +Y, +Z
        # pos.xyz + col.rgb
        axis_len = 1.0
        cone_height = 0.18  # keep this value the same as your cone_height below
        line_end = axis_len - cone_height

        import math

        def add_cone(verts_list, tip, axis_dir, radius, height, segments, col):
            """
            Adds a triangle fan-ish cone (side triangles only).
            tip: (x,y,z)
            axis_dir: normalized direction for the cone axis
            radius: base radius
            height: length from base center to tip (tip is at the end)
            """
            tx, ty, tz = tip
            dx, dy, dz = axis_dir

            # base center is tip - axis_dir * height
            bx = tx - dx * height
            by = ty - dy * height
            bz = tz - dz * height

            # build an orthonormal basis (u,v) perpendicular to axis_dir
            # pick a helper vector not parallel to axis_dir
            if abs(dx) < 0.9:
                hx, hy, hz = (1.0, 0.0, 0.0)
            else:
                hx, hy, hz = (0.0, 1.0, 0.0)

            # u = normalize(axis x helper)
            ux = dy * hz - dz * hy
            uy = dz * hx - dx * hz
            uz = dx * hy - dy * hx
            ulen = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
            ux, uy, uz = ux / ulen, uy / ulen, uz / ulen

            # v = axis x u
            vx = dy * uz - dz * uy
            vy = dz * ux - dx * uz
            vz = dx * uy - dy * ux

            r, g, b = col

            # side triangles: tip -> rim_i -> rim_{i+1}
            for i in range(segments):
                a0 = (i / segments) * (math.pi * 2.0)
                a1 = ((i + 1) / segments) * (math.pi * 2.0)

                x0 = bx + radius * (math.cos(a0) * ux + math.sin(a0) * vx)
                y0 = by + radius * (math.cos(a0) * uy + math.sin(a0) * vy)
                z0 = bz + radius * (math.cos(a0) * uz + math.sin(a0) * vz)

                x1 = bx + radius * (math.cos(a1) * ux + math.sin(a1) * vx)
                y1 = by + radius * (math.cos(a1) * uy + math.sin(a1) * vy)
                z1 = bz + radius * (math.cos(a1) * uz + math.sin(a1) * vz)

                # Triangle: tip, rim0, rim1
                verts_list.extend([tx, ty, tz, r, g, b])
                verts_list.extend([x0, y0, z0, r, g, b])
                verts_list.extend([x1, y1, z1, r, g, b])

        axis_len = 1.0

        verts = []

        # ---- LINES (6 vertices) ----
        # Make the line stop at the cone base (so it doesn't poke through the cone tip)
        axis_len = 1.0
        cone_height = 0.18
        line_end = axis_len - cone_height

        # X axis
        verts.extend([0.0, 0.0, 0.0,  1.0, 0.0, 0.0])
        verts.extend([line_end, 0.0, 0.0,  1.0, 0.0, 0.0])

        # Y axis
        verts.extend([0.0, 0.0, 0.0,  0.0, 1.0, 0.0])
        verts.extend([0.0, line_end, 0.0,  0.0, 1.0, 0.0])

        # Z axis
        verts.extend([0.0, 0.0, 0.0,  0.0, 0.0, 1.0])
        verts.extend([0.0, 0.0, line_end,  0.0, 0.0, 1.0])

        self._line_vert_count = 6

        # ---- CONES (triangles) ----
        # Small cones near ends
        cone_radius = 0.06
        cone_height = 0.18
        segs = 12

        # Tip at each axis end, cone points outward along axis direction
        add_cone(verts, tip=(axis_len, 0.0, 0.0), axis_dir=(1.0, 0.0, 0.0),
                 radius=cone_radius, height=cone_height, segments=segs, col=(1.0, 0.0, 0.0))
        add_cone(verts, tip=(0.0, axis_len, 0.0), axis_dir=(0.0, 1.0, 0.0),
                 radius=cone_radius, height=cone_height, segments=segs, col=(0.0, 1.0, 0.0))
        add_cone(verts, tip=(0.0, 0.0, axis_len), axis_dir=(0.0, 0.0, 1.0),
                 radius=cone_radius, height=cone_height, segments=segs, col=(0.0, 0.0, 1.0))

        # total vertices after the first 6 are triangles
        total_vert_count = len(verts) // 6
        self._tri_vert_count = total_vert_count - self._line_vert_count

        data = struct.pack(f"{len(verts)}f", *verts)

        data = struct.pack(f"{len(verts)}f", *verts)

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(data, len(data))

        self._vao = QOpenGLVertexArrayObject()
        self._vao.create()
        self._vao.bind()

        self._prog.bind()

        stride = 6 * 4
        self._prog.enableAttributeArray(0)
        self._prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
        self._prog.enableAttributeArray(1)
        self._prog.setAttributeBuffer(1, GL_FLOAT, 3 * 4, 3, stride)

        self._vao.release()
        self._vbo.release()
        self._prog.release()

        # Basic state
        self._gl.glEnable(GL_DEPTH_TEST)
        self._gl.glLineWidth(3.0)

    def paintGL(self) -> None:
        if not self._gl or not self._prog or not self._vao:
            return

        w = max(1, self.width())
        h = max(1, self.height())
        aspect = float(w) / float(h)

        self._gl.glViewport(0, 0, w, h)
        self._gl.glClearColor(0.08, 0.08, 0.10, 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        # Build a simple camera MVP using Qt math types
        proj = QtGui.QMatrix4x4()
        proj.perspective(45.0, aspect, 0.01, 100.0)

        view = QtGui.QMatrix4x4()
        # Orbit camera around origin
        cam = QtGui.QVector3D(2.0, 1.5, 2.0)
        view.lookAt(cam, QtGui.QVector3D(0.0, 0.0, 0.0), QtGui.QVector3D(0.0, 1.0, 0.0))

        model = QtGui.QMatrix4x4()
        model.rotate(self._t * 30.0, 0.0, 1.0, 0.0)

        mvp = proj * view * model

        self._prog.bind()
        self._prog.setUniformValue("u_mvp", mvp)

        self._vao.bind()
                # Draw axis lines
        self._gl.glDrawArrays(GL_LINES, 0, self._line_vert_count)

        # Draw cone triangles (start after the line vertices)
        self._gl.glDrawArrays(GL_TRIANGLES, self._line_vert_count, self._tri_vert_count)

        self._vao.release()

        self._prog.release()


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = Smoke3D()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
