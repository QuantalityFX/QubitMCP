from __future__ import annotations

import numpy as np


def project_world(world_xyz, PV, vw, vh):
    p = np.array([world_xyz[0], world_xyz[1], world_xyz[2], 1.0], dtype="f4")
    c = PV @ p
    if abs(float(c[3])) < 1e-8:
        return None
    ndc = c[:3] / c[3]
    sx = (ndc[0] * 0.5 + 0.5) * vw
    sy = (1.0 - (ndc[1] * 0.5 + 0.5)) * vh
    return float(sx), float(sy)


def project_local(local_xyz, PVTRS, vw, vh):
    p = np.array([local_xyz[0], local_xyz[1], local_xyz[2], 1.0], dtype="f4")
    c = PVTRS @ p
    if abs(float(c[3])) < 1e-8:
        return None
    ndc = c[:3] / c[3]
    sx = (ndc[0] * 0.5 + 0.5) * vw
    sy = (1.0 - (ndc[1] * 0.5 + 0.5)) * vh
    return float(sx), float(sy)


def dist_pt_seg(px2, py2, ax, ay, bx, by):
    abx = bx - ax
    aby = by - ay
    apx = px2 - ax
    apy = py2 - ay
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-8:
        dx = px2 - ax
        dy = py2 - ay
        return (dx * dx + dy * dy) ** 0.5
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx = ax + t * abx
    cy = ay + t * aby
    dx = px2 - cx
    dy = py2 - cy
    return (dx * dx + dy * dy) ** 0.5


def axis_proj_max_len(p0, axis_proj):
    max_axis_len = 0.0
    for p1 in axis_proj.values():
        if p1 is None:
            continue
        dx1 = float(p1[0]) - float(p0[0])
        dy1 = float(p1[1]) - float(p0[1])
        dist = (dx1 * dx1 + dy1 * dy1) ** 0.5
        if dist > max_axis_len:
            max_axis_len = dist
    return max_axis_len
