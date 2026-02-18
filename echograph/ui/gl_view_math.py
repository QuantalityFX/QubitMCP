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


def plane_normal_from_vm(V, M):
    try:
        invVM = np.linalg.inv((np.asarray(V, dtype=np.float32) @ np.asarray(M, dtype=np.float32)).astype(np.float32))
        n = -invVM[:3, 2]
        nlen = float(np.linalg.norm(n))
        if nlen > 1e-6:
            return n / nlen
    except Exception:
        pass
    return None


def ray_from_screen(invPV, px, py, vw, vh):
    try:
        x = (2.0 * (px / max(1.0, vw))) - 1.0
        y = 1.0 - (2.0 * (py / max(1.0, vh)))
        near = np.array([x, y, -1.0, 1.0], dtype="f4")
        far = np.array([x, y, 1.0, 1.0], dtype="f4")
        pN = invPV @ near
        pF = invPV @ far
        pN = pN[:3] / pN[3]
        pF = pF[:3] / pF[3]
        ray_o = pN.astype("f4")
        ray_d = (pF - pN).astype("f4")
        rn = float(np.linalg.norm(ray_d))
        if rn > 1e-8:
            ray_d /= rn
            return ray_o, ray_d
    except Exception:
        pass
    return None


def plane_hit(point_on_plane, plane_normal, ray_o, ray_d):
    denom = float(np.dot(ray_d, plane_normal))
    if abs(denom) <= 1e-6:
        return None
    t = float(np.dot((point_on_plane - ray_o), plane_normal)) / denom
    return ray_o + (t * ray_d)


def axis_line_ray_param(axis_world, g0, ray_o, ray_d):
    w0 = ray_o - g0
    ad = float(np.dot(axis_world, ray_d))
    denom = 1.0 - ad * ad
    if abs(denom) < 1e-6:
        return float(np.dot(axis_world, w0))
    return float((ad * float(np.dot(ray_d, w0)) - float(np.dot(axis_world, w0))) / denom)


def unwrap_deg(prev_deg, new_deg_wrapped):
    d = float(new_deg_wrapped) - float(prev_deg)
    out = float(new_deg_wrapped)
    if d > 180.0:
        out -= 360.0
    elif d < -180.0:
        out += 360.0
    return out


def closest_unwrapped_euler(candidates, current_xyz):
    cx, cy, cz = float(current_xyz[0]), float(current_xyz[1]), float(current_xyz[2])
    best = None
    best_err = 1e30
    for ax, ay, az in candidates:
        ux = unwrap_deg(cx, ax)
        uy = unwrap_deg(cy, ay)
        uz = unwrap_deg(cz, az)
        err = (ux - cx) * (ux - cx) + (uy - cy) * (uy - cy) + (uz - cz) * (uz - cz)
        if err < best_err:
            best_err = err
            best = (ux, uy, uz)
    return best


def gizmo_screen_scale(P, V, M, T, R, viewport_height_px, scale_multiplier, target_ring_px, ring_radius):
    try:
        vh_s = float(max(1.0, viewport_height_px))
        Pn = np.asarray(P, dtype=np.float32)
        Vn = np.asarray(V, dtype=np.float32)
        Mn = np.asarray(M, dtype=np.float32)
        proj_y = abs(float(Pn[1, 1]))
        if proj_y <= 1e-6:
            return 1.0

        vm = (Vn @ Mn @ (T @ R)).astype(np.float32)
        cp = vm @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
        w = float(cp[3]) if abs(float(cp[3])) > 1e-6 else 1.0
        dist_raw = abs(float(cp[2]) / w)
        dist_raw = max(dist_raw, 1e-6)
        dist = dist_raw * float(scale_multiplier)

        ring_r = float(ring_radius)
        if ring_r <= 1e-6:
            return 1.0

        scene_scale = 1.0
        try:
            sx = float(np.linalg.norm(Mn[:3, 0]))
            sy = float(np.linalg.norm(Mn[:3, 1]))
            sz = float(np.linalg.norm(Mn[:3, 2]))
            scene_scale = (sx + sy + sz) / 3.0
            if scene_scale <= 1e-6:
                scene_scale = 1.0
        except Exception:
            scene_scale = 1.0

        s = (float(target_ring_px) * 2.0 * dist) / (vh_s * proj_y * ring_r * scene_scale)
        return max(1e-6, min(1000.0, float(s)))
    except Exception:
        return 1.0
