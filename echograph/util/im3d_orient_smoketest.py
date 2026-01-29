# echograph/util/im3d_orient_smoketest.py
from __future__ import annotations

import sys
import math
import struct
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtGui import QSurfaceFormat
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
GL_BLEND = 0x0BE2
GL_DITHER = 0x0BD0
GL_MULTISAMPLE = 0x809D
GL_FRAMEBUFFER_SRGB = 0x8DB9
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
        self._rx = 0.0
        self._ry = 0.0
        self._rz = 0.0
        self._orient = QtGui.QQuaternion()
        self._active_axis = "y"

        self.setMinimumSize(900, 600)
        self.setWindowTitle("Qt GL Orient Smoke Test (3D Axis)")

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)
        self._line_vert_count = 0
        self._tri_vert_count = 0
        self._axis_checks = {}
        self._axis_panel = QtWidgets.QWidget(self)
        panel_layout = QtWidgets.QHBoxLayout(self._axis_panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(10)
        axis_colors = {
            "x": "#ff0000",
            "y": "#00ff00",
            "z": "#0000ff",
        }
        for label in ("X", "Y", "Z"):
            cb = QtWidgets.QCheckBox(label, self._axis_panel)
            axis_color = axis_colors[label.lower()]
            cb.setStyleSheet(
                "QCheckBox { color: " + axis_color + "; }"
                "QCheckBox::indicator {"
                " width: 14px; height: 14px;"
                " border: 1px solid " + axis_color + ";"
                " background: transparent;"
                "}"
                "QCheckBox::indicator:checked {"
                " background-color: " + axis_color + ";"
                "}"
            )
            cb.stateChanged.connect(lambda state, axis=label.lower(): self._on_axis_toggle(axis, state))
            panel_layout.addWidget(cb)
            self._axis_checks[label.lower()] = cb
        self._axis_checks["y"].setChecked(True)
        reset_btn = QtWidgets.QPushButton("Reset", self._axis_panel)
        reset_btn.clicked.connect(self._reset_axes)
        panel_layout.addWidget(reset_btn)
        copy_btn = QtWidgets.QPushButton("Copy", self._axis_panel)
        copy_btn.clicked.connect(self._copy_axes)
        panel_layout.addWidget(copy_btn)
        self._axis_panel.adjustSize()
        self._axis_panel.move(10, 36)

    def _reset_axes(self) -> None:
        self._rx = 0.0
        self._ry = 0.0
        self._rz = 0.0
        self._orient = QtGui.QQuaternion()
        self.update()

    def _copy_axes(self) -> None:
        rx = self._rx if self._rx <= 180.0 else self._rx - 360.0
        ry = self._ry if self._ry <= 180.0 else self._ry - 360.0
        rz = self._rz if self._rz <= 180.0 else self._rz - 360.0
        text = f"RX={rx:+.1f} RY={ry:+.1f} RZ={rz:+.1f}"
        try:
            QtWidgets.QApplication.clipboard().setText(text)
        except Exception:
            pass

    def _on_axis_toggle(self, axis: str, state: int) -> None:
        if state:
            for other, cb in self._axis_checks.items():
                if other != axis and cb.isChecked():
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)
            self._active_axis = axis
        else:
            if self._active_axis == axis:
                self._active_axis = None
        self.update()

    def _tick(self):
        dt = 0.016
        self._t += dt
        speed = 45.0  # degrees per second (positive)
        axis_vec = None
        if self._active_axis == "x":
            axis_vec = QtGui.QVector3D(1.0, 0.0, 0.0)
        elif self._active_axis == "y":
            axis_vec = QtGui.QVector3D(0.0, 1.0, 0.0)
        elif self._active_axis == "z":
            axis_vec = QtGui.QVector3D(0.0, 0.0, 1.0)
        if axis_vec is not None:
            try:
                axis_world = self._orient.rotatedVector(axis_vec)
            except Exception:
                axis_world = axis_vec
            q_delta = QtGui.QQuaternion.fromAxisAndAngle(axis_world, speed * dt)
            try:
                self._orient = (q_delta * self._orient).normalized()
            except Exception:
                self._orient = q_delta * self._orient

        rx, ry, rz = self._euler_from_quat(self._orient)
        self._rx, self._ry, self._rz = rx, ry, rz
        self.setWindowTitle(f"Qt GL Orient Smoke Test | RX={rx:+.1f} RY={ry:+.1f} RZ={rz:+.1f}")
        self.update()

    def _euler_from_quat(self, q: QtGui.QQuaternion):
        try:
            w = float(q.scalar())
            x = float(q.x())
            y = float(q.y())
            z = float(q.z())
        except Exception:
            return 0.0, 0.0, 0.0

        # Quaternion to rotation matrix (row-major).
        xx = x * x
        yy = y * y
        zz = z * z
        xy = x * y
        xz = x * z
        yz = y * z
        wx = w * x
        wy = w * y
        wz = w * z

        R = [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy + wz), 2.0 * (xz - wy)],
            [2.0 * (xy - wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz + wx)],
            [2.0 * (xz + wy), 2.0 * (yz - wx), 1.0 - 2.0 * (xx + yy)],
        ]

        # Match renderer order: Rz @ Ry @ Rx (row-vector convention).
        Rt = [
            [R[0][0], R[1][0], R[2][0]],
            [R[0][1], R[1][1], R[2][1]],
            [R[0][2], R[1][2], R[2][2]],
        ]
        sy = Rt[0][2]
        if sy > 1.0:
            sy = 1.0
        elif sy < -1.0:
            sy = -1.0
        cy = math.sqrt(max(0.0, 1.0 - sy * sy))
        if cy > 1e-6:
            rx = math.degrees(math.atan2(-Rt[1][2], Rt[2][2]))
            ry = math.degrees(math.atan2(sy, cy))
            rz = math.degrees(math.atan2(-Rt[0][1], Rt[0][0]))
        else:
            rx = math.degrees(math.atan2(Rt[2][1], Rt[1][1]))
            ry = math.degrees(math.atan2(sy, cy))
            rz = 0.0
        return rx, ry, rz

    def initializeGL(self) -> None:
        ctx = self.context()
        self._gl = ctx.functions()
        self._gl.initializeOpenGLFunctions()

        self._prog = QOpenGLShaderProgram()
        self._prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG)
        ok = self._prog.link()
        print("[SMOKE3D_ORIENT] program link:", ok)

        def add_cube(verts_list, center, size, col):
            cx, cy, cz = center
            h = size * 0.5
            r, g, b = col
            # 8 corners
            p = [
                (cx - h, cy - h, cz - h),
                (cx + h, cy - h, cz - h),
                (cx + h, cy + h, cz - h),
                (cx - h, cy + h, cz - h),
                (cx - h, cy - h, cz + h),
                (cx + h, cy - h, cz + h),
                (cx + h, cy + h, cz + h),
                (cx - h, cy + h, cz + h),
            ]

            # triangles for each face (12 triangles)
            faces = [
                (0, 1, 2, 3),  # -Z
                (4, 5, 6, 7),  # +Z
                (0, 4, 7, 3),  # -X
                (1, 5, 6, 2),  # +X
                (0, 1, 5, 4),  # -Y
                (3, 2, 6, 7),  # +Y
            ]
            for a, b, c, d in faces:
                pa, pb, pc, pd = p[a], p[b], p[c], p[d]
                verts_list.extend([pa[0], pa[1], pa[2], r, g, b])
                verts_list.extend([pb[0], pb[1], pb[2], r, g, b])
                verts_list.extend([pc[0], pc[1], pc[2], r, g, b])
                verts_list.extend([pa[0], pa[1], pa[2], r, g, b])
                verts_list.extend([pc[0], pc[1], pc[2], r, g, b])
                verts_list.extend([pd[0], pd[1], pd[2], r, g, b])

        verts = []

        col_x = (1.0, 0.0, 0.0)
        col_y = (0.0, 1.0, 0.0)
        col_z = (0.0, 0.0, 1.0)

        axis_len = 1.0
        cube_size = 0.14
        line_end = axis_len - cube_size * 0.65

        # ---- LINES (6 vertices) ----
        verts.extend([0.0, 0.0, 0.0,  col_x[0], col_x[1], col_x[2]])
        verts.extend([line_end, 0.0, 0.0,  col_x[0], col_x[1], col_x[2]])

        verts.extend([0.0, 0.0, 0.0,  col_y[0], col_y[1], col_y[2]])
        verts.extend([0.0, line_end, 0.0,  col_y[0], col_y[1], col_y[2]])

        verts.extend([0.0, 0.0, 0.0,  col_z[0], col_z[1], col_z[2]])
        verts.extend([0.0, 0.0, line_end,  col_z[0], col_z[1], col_z[2]])

        self._line_vert_count = 6

        # ---- CUBES (triangles) ----
        add_cube(verts, center=(axis_len, 0.0, 0.0), size=cube_size, col=col_x)
        add_cube(verts, center=(0.0, axis_len, 0.0), size=cube_size, col=col_y)
        add_cube(verts, center=(0.0, 0.0, axis_len), size=cube_size, col=col_z)

        total_vert_count = len(verts) // 6
        self._tri_vert_count = total_vert_count - self._line_vert_count

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

        self._gl.glEnable(GL_DEPTH_TEST)
        # Keep colors as literal as possible (no blending/dithering).
        try:
            self._gl.glDisable(GL_BLEND)
            self._gl.glDisable(GL_DITHER)
            self._gl.glDisable(GL_FRAMEBUFFER_SRGB)
            self._gl.glDisable(GL_MULTISAMPLE)
        except Exception:
            pass
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

        proj = QtGui.QMatrix4x4()
        proj.perspective(45.0, aspect, 0.01, 100.0)

        view = QtGui.QMatrix4x4()
        cam = QtGui.QVector3D(2.0, 1.5, 2.0)
        view.lookAt(cam, QtGui.QVector3D(0.0, 0.0, 0.0), QtGui.QVector3D(0.0, 1.0, 0.0))

        model = QtGui.QMatrix4x4()
        try:
            model.rotate(self._orient)
        except Exception:
            model.rotate(self._rx, 1.0, 0.0, 0.0)
            model.rotate(self._ry, 0.0, 1.0, 0.0)
            model.rotate(self._rz, 0.0, 0.0, 1.0)

        mvp = proj * view * model

        self._prog.bind()
        self._prog.setUniformValue("u_mvp", mvp)

        self._vao.bind()
        self._gl.glDrawArrays(GL_LINES, 0, self._line_vert_count)
        self._gl.glDrawArrays(GL_TRIANGLES, self._line_vert_count, self._tri_vert_count)
        self._vao.release()

        self._prog.release()

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QColor(230, 230, 230))
        rx = self._rx if self._rx <= 180.0 else self._rx - 360.0
        ry = self._ry if self._ry <= 180.0 else self._ry - 360.0
        rz = self._rz if self._rz <= 180.0 else self._rz - 360.0
        painter.drawText(12, 22, f"RX: {rx:+.1f} deg  RY: {ry:+.1f} deg  RZ: {rz:+.1f} deg")
        painter.end()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        try:
            self._axis_panel.adjustSize()
            self._axis_panel.move(10, 36)
        except Exception:
            pass
        super().resizeEvent(e)


def main():
    # Force single-sample (no MSAA) at the Qt surface level.
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setVersion(3, 3)
    fmt.setSamples(0)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QtWidgets.QApplication(sys.argv)
    w = Smoke3D()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
