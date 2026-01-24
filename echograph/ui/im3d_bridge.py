# echograph/util/im3d_smoketest.py
from __future__ import annotations

import sys
import struct
from PySide6 import QtWidgets, QtCore
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import (
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLBuffer,
    QOpenGLVertexArrayObject,
)

VERT = """
#version 330
layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_col;
out vec3 v_col;
void main() {
    gl_Position = vec4(in_pos, 1.0);
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


class Smoke(QOpenGLWidget):
    def __init__(self):
        super().__init__()
        self._gl = None
        self._prog: QOpenGLShaderProgram | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._vao: QOpenGLVertexArrayObject | None = None

        self.setMinimumSize(900, 600)
        self.setWindowTitle("Qt GL Smoke Test (Axis)")

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(16)

    def initializeGL(self) -> None:
        ctx = self.context()
        self._gl = ctx.functions()
        self._gl.initializeOpenGLFunctions()

        self._prog = QOpenGLShaderProgram()
        self._prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG)
        ok = self._prog.link()
        print("[SMOKE] program link:", ok)

        # Big axis in clip space so it must show if drawing works
        verts = [
            -0.8, 0.0, 0.0,   1.0, 0.0, 0.0,
             0.8, 0.0, 0.0,   1.0, 0.0, 0.0,

             0.0,-0.8, 0.0,   0.0, 1.0, 0.0,
             0.0, 0.8, 0.0,   0.0, 1.0, 0.0,

             0.0, 0.0,-0.8,   0.0, 0.0, 1.0,
             0.0, 0.0, 0.8,   0.0, 0.0, 1.0,
        ]
        data = struct.pack(f"{len(verts)}f", *verts)

        self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(data, len(data))

        self._vao = QOpenGLVertexArrayObject()
        self._vao.create()
        self._vao.bind()

        self._prog.bind()

        # GL_FLOAT = 0x1406
        stride = 6 * 4
        self._prog.enableAttributeArray(0)
        self._prog.setAttributeBuffer(0, 0x1406, 0, 3, stride)
        self._prog.enableAttributeArray(1)
        self._prog.setAttributeBuffer(1, 0x1406, 3 * 4, 3, stride)

        self._vao.release()
        self._vbo.release()
        self._prog.release()

        # State: draw on top
        self._gl.glDisable(0x0B71)  # GL_DEPTH_TEST
        self._gl.glLineWidth(3.0)

    def paintGL(self) -> None:
        if not self._gl or not self._prog or not self._vao:
            return

        self._gl.glViewport(0, 0, self.width(), self.height())
        self._gl.glClearColor(0.1, 0.1, 0.1, 1.0)
        self._gl.glClear(0x00004000 | 0x00000100)  # GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT

        self._prog.bind()
        self._vao.bind()
        self._gl.glDrawArrays(0x0001, 0, 6)  # GL_LINES
        self._vao.release()
        self._prog.release()


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = Smoke()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
