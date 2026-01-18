# echograph/ui/gl_arcball.py
from __future__ import annotations

try:
    import numpy as np
except Exception:
    np = None


class _ArcBall:
    def __init__(self, width: float, height: float):
        if np is None:
            raise RuntimeError("numpy unavailable")
        self.StVec = np.zeros(3, "f4")
        self.EnVec = np.zeros(3, "f4")
        self.AdjustWidth = 0.0
        self.AdjustHeight = 0.0
        self.Epsilon = 1.0e-5
        self.setBounds(width, height)

    def setBounds(self, width: float, height: float) -> None:
        width = max(2.0, float(width))
        height = max(2.0, float(height))
        self.AdjustWidth = 1.0 / ((width - 1.0) * 0.5)
        self.AdjustHeight = 1.0 / ((height - 1.0) * 0.5)

    def click(self, point: "np.ndarray") -> None:
        self._mapToSphere(point, self.StVec)

    def drag(self, point: "np.ndarray") -> "np.ndarray":
        new_rot = np.zeros((4,), "f4")
        self._mapToSphere(point, self.EnVec)
        perp = np.cross(self.StVec, self.EnVec)
        if np.linalg.norm(perp) > self.Epsilon:
            new_rot[:3] = perp[:3]
            new_rot[3] = np.dot(self.StVec, self.EnVec)
        return new_rot

    def _mapToSphere(self, point: "np.ndarray", new_vec: "np.ndarray") -> None:
        temp = point.copy()
        temp[0] = (temp[0] * self.AdjustWidth) - 1.0
        temp[1] = 1.0 - (temp[1] * self.AdjustHeight)
        length2 = np.dot(temp, temp)
        if length2 > 1.0:
            norm = 1.0 / np.sqrt(length2)
            new_vec[0] = temp[0] * norm
            new_vec[1] = temp[1] * norm
            new_vec[2] = 0.0
        else:
            new_vec[0] = temp[0]
            new_vec[1] = temp[1]
            new_vec[2] = np.sqrt(1.0 - length2)


class _ArcBallUtil(_ArcBall):
    def __init__(self, width: float, height: float):
        if np is None:
            raise RuntimeError("numpy unavailable")
        self.Transform = np.identity(4, "f4")
        self.LastRot = np.identity(3, "f4")
        self.ThisRot = np.identity(3, "f4")
        self.isDragging = False
        super().__init__(width, height)

    def onDrag(self, cursor_x: float, cursor_y: float) -> None:
        if not self.isDragging:
            return
        mouse_pt = np.array([cursor_x, cursor_y], "f4")
        quat = self.drag(mouse_pt)
        quat[0] *= 0.5
        self.ThisRot = self._quat_to_mat3(quat)
        self.ThisRot = np.matmul(self.LastRot, self.ThisRot)
        self.Transform = self._set_rotation(self.Transform, self.ThisRot)

    def resetRotation(self) -> None:
        self.isDragging = False
        self.LastRot = np.identity(3, "f4")
        self.ThisRot = np.identity(3, "f4")
        self.Transform = self._set_rotation(self.Transform, self.ThisRot)

    def onClickLeftUp(self) -> None:
        self.isDragging = False
        self.LastRot = self.ThisRot.copy()

    def onClickLeftDown(self, cursor_x: float, cursor_y: float) -> None:
        self.LastRot = self.ThisRot.copy()
        self.isDragging = True
        mouse_pt = np.array([cursor_x, cursor_y], "f4")
        self.click(mouse_pt)

    @staticmethod
    def _set_rotation(obj: "np.ndarray", m3x3: "np.ndarray") -> "np.ndarray":
        scale = np.linalg.norm(obj[:3, :3], ord="fro") / np.sqrt(3)
        obj[0:3, 0:3] = m3x3 * scale
        return obj

    def _quat_to_mat3(self, q: "np.ndarray") -> "np.ndarray":
        if np.sum(np.dot(q, q)) < self.Epsilon:
            return np.identity(3, "f4")
        norm = np.linalg.norm(q)
        if norm <= self.Epsilon:
            return np.identity(3, "f4")
        q = q / norm
        x, y, z, w = q
        xx = x * x
        yy = y * y
        zz = z * z
        xy = x * y
        xz = x * z
        yz = y * z
        wx = w * x
        wy = w * y
        wz = w * z
        return np.array(
            [
                [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
                [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
                [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
            ],
            dtype="f4",
        ).T
