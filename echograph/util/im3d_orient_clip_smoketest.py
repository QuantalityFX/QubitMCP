# echograph/util/im3d_orient_clip_smoketest.py
from __future__ import annotations

import math
import struct
import sys

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtOpenGL import (
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLBuffer,
        QOpenGLVertexArrayObject,
    )
    _HAS_QT6 = True
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
    from PySide2.QtGui import QSurfaceFormat  # type: ignore
    from PySide2.QtWidgets import QOpenGLWidget  # type: ignore
    from PySide2.QtOpenGL import (  # type: ignore
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLBuffer,
        QOpenGLVertexArrayObject,
    )
    _HAS_QT6 = False

# GL enum constants (no PyOpenGL dependency)
GL_FLOAT = 0x1406
GL_LINES = 0x0001
GL_DEPTH_TEST = 0x0B71
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100

VERT = """
#version 330
layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_col;
uniform mat4 u_mvp;
out vec3 v_col;
out vec3 v_pos;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_col = in_col;
    v_pos = in_pos;
}
"""

FRAG = """
#version 330
in vec3 v_col;
in vec3 v_pos;
out vec4 fragColor;
uniform vec3 u_clip;
uniform vec3 u_view_dir;
uniform vec3 u_view_right;
uniform vec3 u_back_clip;
void main() {
    if (u_clip.x > 0.5) {
        vec3 p = v_pos;
        vec3 axis;
        float eps = 1e-5;
        if (abs(p.x) < eps) {
            axis = vec3(1.0, 0.0, 0.0);
        } else if (abs(p.y) < eps) {
            axis = vec3(0.0, 1.0, 0.0);
        } else {
            axis = vec3(0.0, 0.0, 1.0);
        }
        vec3 proj = u_view_dir - axis * dot(u_view_dir, axis);
        float proj_len = length(proj);
        // If looking straight down the axis (ring faces camera), don't clip.
        if (proj_len > 1e-4) {
            proj /= proj_len;
            vec3 ring_dir = p - axis * dot(p, axis);
            float ring_len = length(ring_dir);
            if (ring_len > 1e-4) {
                ring_dir /= ring_len;
                float d = dot(ring_dir, proj);
                if (d < u_back_clip.x) {
                    discard;
                }
            }
        }
    }
    fragColor = vec4(v_col, 1.0);
}
"""


class GizmoClipSmoke(QOpenGLWidget):
    def __init__(self) -> None:
        super().__init__()
        self._gl = None
        self._prog: QOpenGLShaderProgram | None = None
        self._vbo: QOpenGLBuffer | None = None
        self._vao: QOpenGLVertexArrayObject | None = None
        self._line_vert_count = 0

        self._t = 0.0
        self._clip_frac = 0.3
        self._clip_enabled = True

        self.setMinimumSize(900, 600)
        self.setWindowTitle("Qt GL Gizmo Clip Smoke Test")

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

        self._panel = QtWidgets.QWidget(self)
        layout = QtWidgets.QHBoxLayout(self._panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)
        self._clip_check = QtWidgets.QCheckBox("Clip", self._panel)
        self._clip_check.setChecked(True)
        self._clip_check.toggled.connect(self._on_clip_toggle)
        layout.addWidget(self._clip_check)
        # Clip UI (disabled for now; keep wiring for quick re-enable).
        self._clip_label = None
        self._clip_slider = None
        # self._clip_label = QtWidgets.QLabel(self._panel)
        # layout.addWidget(self._clip_label)
        # self._clip_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, self._panel)
        # self._clip_slider.setRange(0, 100)
        # self._clip_slider.setValue(int(self._clip_frac * 100))
        # self._clip_slider.setFixedWidth(180)
        # self._clip_slider.valueChanged.connect(self._on_clip_changed)
        # layout.addWidget(self._clip_slider)
        self._panel.adjustSize()
        self._panel.move(10, 10)
        self._update_clip_label()

    def _tick(self) -> None:
        self._t += 0.016
        self.update()

    def _on_clip_toggle(self, on: bool) -> None:
        self._clip_enabled = bool(on)
        self.update()

    def _on_clip_changed(self, value: int) -> None:
        self._clip_frac = max(0.0, min(1.0, float(value) / 100.0))
        self._update_clip_label()
        self.update()

    def _update_clip_label(self) -> None:
        pct = int(round(self._clip_frac * 100.0))
        label = getattr(self, "_clip_label", None)
        if label is None:
            return
        label.setText(f"Back Clip {pct}%")

    def initializeGL(self) -> None:
        ctx = self.context()
        self._gl = ctx.functions()
        self._gl.initializeOpenGLFunctions()

        self._prog = QOpenGLShaderProgram()
        self._prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG)
        self._prog.link()

        verts: list[float] = []

        def add_circle(axis: str, radius: float, segments: int, col):
            r, g, b = col
            for i in range(segments):
                a0 = (i / segments) * (math.pi * 2.0)
                a1 = ((i + 1) / segments) * (math.pi * 2.0)

                if axis == "x":
                    p0 = (0.0, radius * math.cos(a0), radius * math.sin(a0))
                    p1 = (0.0, radius * math.cos(a1), radius * math.sin(a1))
                elif axis == "y":
                    p0 = (radius * math.cos(a0), 0.0, radius * math.sin(a0))
                    p1 = (radius * math.cos(a1), 0.0, radius * math.sin(a1))
                else:  # "z"
                    p0 = (radius * math.cos(a0), radius * math.sin(a0), 0.0)
                    p1 = (radius * math.cos(a1), radius * math.sin(a1), 0.0)

                verts.extend([p0[0], p0[1], p0[2], r, g, b])
                verts.extend([p1[0], p1[1], p1[2], r, g, b])

        radius = 0.9
        segs = 64
        add_circle("x", radius, segs, (1.0, 0.0, 0.0))
        add_circle("y", radius, segs, (0.0, 1.0, 0.0))
        add_circle("z", radius, segs, (0.0, 0.0, 1.0))
        self._line_vert_count = len(verts) // 6

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

        try:
            self._gl.glDisable(GL_DEPTH_TEST)
        except Exception:
            pass
        self._gl.glLineWidth(4.0)

    def paintGL(self) -> None:
        if not self._gl or not self._prog or not self._vao:
            return

        w = max(1, self.width())
        h = max(1, self.height())
        aspect = float(w) / float(h)

        self._gl.glViewport(0, 0, w, h)
        self._gl.glClearColor(0.08, 0.08, 0.10, 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        proj = QtGui.QMatrix4x4()
        proj.perspective(45.0, aspect, 0.01, 100.0)

        yaw = self._t * 0.5
        pitch = 0.4
        dist = 3.2
        cam = QtGui.QVector3D(
            dist * math.cos(pitch) * math.sin(yaw),
            dist * math.sin(pitch),
            dist * math.cos(pitch) * math.cos(yaw),
        )
        view = QtGui.QMatrix4x4()
        view.lookAt(cam, QtGui.QVector3D(0.0, 0.0, 0.0), QtGui.QVector3D(0.0, 1.0, 0.0))

        model = QtGui.QMatrix4x4()
        mvp = proj * view * model

        view_len = math.sqrt(float(cam.x()) ** 2 + float(cam.y()) ** 2 + float(cam.z()) ** 2)
        if view_len > 1e-6:
            view_dir = QtGui.QVector3D(
                float(cam.x()) / view_len,
                float(cam.y()) / view_len,
                float(cam.z()) / view_len,
            )
        else:
            view_dir = QtGui.QVector3D(0.0, 0.0, 1.0)
        back_clip_cos = -math.cos(math.pi * float(self._clip_frac))

        self._prog.bind()
        self._prog.setUniformValue("u_mvp", mvp)
        clip_val = 1.0 if self._clip_enabled else 0.0
        self._prog.setUniformValue(
            "u_clip",
            QtGui.QVector3D(float(clip_val), 0.0, 0.0),
        )
        self._prog.setUniformValue("u_view_dir", view_dir)
        try:
            up = QtGui.QVector3D(0.0, 1.0, 0.0)
            right = QtGui.QVector3D.crossProduct(up, view_dir)
            if right.length() <= 1e-6:
                right = QtGui.QVector3D(1.0, 0.0, 0.0)
            else:
                right /= right.length()
        except Exception:
            right = QtGui.QVector3D(1.0, 0.0, 0.0)
        self._prog.setUniformValue("u_view_right", right)
        self._prog.setUniformValue(
            "u_back_clip",
            QtGui.QVector3D(float(back_clip_cos), 0.0, 0.0),
        )
        self._vao.bind()
        self._gl.glDrawArrays(GL_LINES, 0, self._line_vert_count)
        self._vao.release()

        self._prog.release()

def main() -> None:
    # Force single-sample (no MSAA) at the Qt surface level.
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setVersion(3, 3)
    fmt.setSamples(0)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QtWidgets.QApplication(sys.argv)
    w = GizmoClipSmoke()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
