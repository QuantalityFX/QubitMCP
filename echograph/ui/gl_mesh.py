# echograph/ui/gl_mesh.py
from __future__ import annotations

import struct
from typing import List

from echograph.qt_compat import QtCore

try:
    from echograph.qt_compat import QtGui  # not always needed, but safe
except Exception:
    QtGui = None

try:
    from echograph.qt_compat import QOpenGLBuffer
except Exception:
    QOpenGLBuffer = None


class _GLMesh:
    def __init__(self, vertices: List[float]):
        self.vertices = vertices
        self.count = max(0, len(vertices) // 3)
        self.vbo = None

    def upload(self):
        if QOpenGLBuffer is None:
            return
        if self.vbo is None:
            self.vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self.vbo.create()
        if not self.vbo.bind():
            return
        data = QtCore.QByteArray(struct.pack(f"{len(self.vertices)}f", *self.vertices))
        self.vbo.allocate(data, data.size())
        self.vbo.release()
