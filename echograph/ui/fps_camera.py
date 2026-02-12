from __future__ import annotations

import math

try:
    import numpy as np
except Exception:
    np = None

try:
    from pyrr import Matrix44
except Exception:
    Matrix44 = None


def _normalize(v: "np.ndarray", fallback: "np.ndarray") -> "np.ndarray":
    ln = float(np.linalg.norm(v))
    if ln < 1e-8:
        return fallback.copy()
    return v / ln


def _rotate(vec: "np.ndarray", axis: "np.ndarray", ang: float) -> "np.ndarray":
    axis = _normalize(axis, np.array([0.0, 1.0, 0.0], dtype=np.float32))
    c = math.cos(ang)
    s = math.sin(ang)
    t = 1.0 - c
    x, y, z = float(axis[0]), float(axis[1]), float(axis[2])
    R = np.array(
        [
            [t * x * x + c,     t * x * y - s * z, t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c,     t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
        ],
        dtype=np.float32,
    )
    return R @ vec


class FpsCamera:
    def __init__(self) -> None:
        if np is None:
            raise RuntimeError("numpy unavailable")
        self.position = np.zeros(3, dtype=np.float32)
        self.forward = np.array([0.0, 0.0, -1.0], dtype=np.float32)
        self.up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        self._orthonormalize()

    def _orthonormalize(self) -> None:
        self.forward = _normalize(self.forward, np.array([0.0, 0.0, -1.0], dtype=np.float32))
        self.up = _normalize(self.up, np.array([0.0, 1.0, 0.0], dtype=np.float32))
        right = np.cross(self.forward, self.up)
        right = _normalize(right, np.array([1.0, 0.0, 0.0], dtype=np.float32))
        self.up = _normalize(np.cross(right, self.forward), np.array([0.0, 1.0, 0.0], dtype=np.float32))

    def lock_roll(self) -> None:
        world_up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        fwd = _normalize(self.forward, np.array([0.0, 0.0, -1.0], dtype=np.float32))
        right = np.cross(fwd, world_up)
        right = _normalize(right, np.array([1.0, 0.0, 0.0], dtype=np.float32))
        self.forward = fwd
        self.up = _normalize(np.cross(right, fwd), world_up)

    def set_from_orbit(self, center: "np.ndarray", rot: "np.ndarray", zoom: float, roll_locked: bool = True) -> None:
        center = np.array(center, dtype=np.float32)
        cam_local = np.array([0.0, 0.0, float(zoom)], dtype=np.float32)
        cam_pos = center + (rot.T @ cam_local)
        forward = center - cam_pos
        if float(np.linalg.norm(forward)) < 1e-8:
            forward = np.array([0.0, 0.0, -1.0], dtype=np.float32)
        up = rot.T @ np.array([0.0, 1.0, 0.0], dtype=np.float32)
        self.position = cam_pos.astype(np.float32)
        self.forward = forward.astype(np.float32)
        self.up = up.astype(np.float32)
        self._orthonormalize()
        if roll_locked:
            self.lock_roll()

    def set_from_view_matrix(self, view_mat: "np.ndarray", roll_locked: bool = True) -> None:
        if np is None:
            return
        try:
            vm = np.array(view_mat, dtype=np.float32)
            inv = np.linalg.inv(vm)
        except Exception:
            return
        try:
            pos = np.array(inv[3, 0:3], dtype=np.float32)
            right = np.array(inv[0, 0:3], dtype=np.float32)
            up = np.array(inv[1, 0:3], dtype=np.float32)
            back = np.array(inv[2, 0:3], dtype=np.float32)
            fwd = -back
        except Exception:
            return
        self.position = pos.astype(np.float32)
        self.forward = fwd.astype(np.float32)
        self.up = up.astype(np.float32)
        self._orthonormalize()
        if roll_locked:
            self.lock_roll()

    def apply_look(self, dx: float, dy: float, sens: float = 0.005, roll_locked: bool = True) -> None:
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return
        yaw = float(-dx) * sens
        pitch = float(-dy) * sens

        if roll_locked:
            world_up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
            self.forward = _rotate(self.forward, world_up, yaw)
            right = np.cross(self.forward, world_up)
            right = _normalize(right, np.array([1.0, 0.0, 0.0], dtype=np.float32))
            self.forward = _rotate(self.forward, right, pitch)
            self.lock_roll()
            return

        right = np.cross(self.forward, self.up)
        right = _normalize(right, np.array([1.0, 0.0, 0.0], dtype=np.float32))
        self.forward = _rotate(self.forward, self.up, yaw)
        right = _rotate(right, self.up, yaw)
        self.forward = _rotate(self.forward, right, pitch)
        self.up = _rotate(self.up, right, pitch)
        self._orthonormalize()

    def move(self, forward_amt: float, right_amt: float, up_amt: float = 0.0, roll_locked: bool = True) -> None:
        right = np.cross(self.forward, self.up)
        right = _normalize(right, np.array([1.0, 0.0, 0.0], dtype=np.float32))
        if roll_locked:
            up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        else:
            up = _normalize(self.up, np.array([0.0, 1.0, 0.0], dtype=np.float32))
        delta = (self.forward * float(forward_amt)) + (right * float(right_amt)) + (up * float(up_amt))
        self.position = (self.position + delta).astype(np.float32)

    def view_matrix(self, roll_locked: bool = True):
        if Matrix44 is None:
            raise RuntimeError("pyrr Matrix44 unavailable")
        if roll_locked:
            self.lock_roll()
        eye = self.position
        target = self.position + self.forward
        up = self.up
        return Matrix44.look_at(tuple(float(x) for x in eye), tuple(float(x) for x in target), tuple(float(x) for x in up))
