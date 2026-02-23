# echograph/ui/gl_debug_geo.py
from __future__ import annotations

import math
from typing import List


def debug_cube_vertices() -> List[float]:
    return [
        # front
        -0.5, -0.5,  0.5,  0.5, -0.5,  0.5,  0.5,  0.5,  0.5,
        -0.5, -0.5,  0.5,  0.5,  0.5,  0.5, -0.5,  0.5,  0.5,
        # back
         0.5, -0.5, -0.5, -0.5, -0.5, -0.5, -0.5,  0.5, -0.5,
         0.5, -0.5, -0.5, -0.5,  0.5, -0.5,  0.5,  0.5, -0.5,
        # left
        -0.5, -0.5, -0.5, -0.5, -0.5,  0.5, -0.5,  0.5,  0.5,
        -0.5, -0.5, -0.5, -0.5,  0.5,  0.5, -0.5,  0.5, -0.5,
        # right
         0.5, -0.5,  0.5,  0.5, -0.5, -0.5,  0.5,  0.5, -0.5,
         0.5, -0.5,  0.5,  0.5,  0.5, -0.5,  0.5,  0.5,  0.5,
        # top
        -0.5,  0.5,  0.5,  0.5,  0.5,  0.5,  0.5,  0.5, -0.5,
        -0.5,  0.5,  0.5,  0.5,  0.5, -0.5, -0.5,  0.5, -0.5,
        # bottom
        -0.5, -0.5, -0.5,  0.5, -0.5, -0.5,  0.5, -0.5,  0.5,
        -0.5, -0.5, -0.5,  0.5, -0.5,  0.5, -0.5, -0.5,  0.5,
    ]


def debug_cube_wire_vertices() -> List[float]:
    return [
        # bottom square
        -0.5, -0.5, -0.5,  0.5, -0.5, -0.5,
         0.5, -0.5, -0.5,  0.5, -0.5,  0.5,
         0.5, -0.5,  0.5, -0.5, -0.5,  0.5,
        -0.5, -0.5,  0.5, -0.5, -0.5, -0.5,
        # top square
        -0.5,  0.5, -0.5,  0.5,  0.5, -0.5,
         0.5,  0.5, -0.5,  0.5,  0.5,  0.5,
         0.5,  0.5,  0.5, -0.5,  0.5,  0.5,
        -0.5,  0.5,  0.5, -0.5,  0.5, -0.5,
        # vertical edges
        -0.5, -0.5, -0.5, -0.5,  0.5, -0.5,
         0.5, -0.5, -0.5,  0.5,  0.5, -0.5,
         0.5, -0.5,  0.5,  0.5,  0.5,  0.5,
        -0.5, -0.5,  0.5, -0.5,  0.5,  0.5,
    ]


def debug_camera_wire_vertices(segments: int = 12) -> List[float]:
    segs = max(6, int(segments))

    # Camera body: rectangular block elongated along Z.
    half_x = 0.171
    half_y = 0.216
    half_z = 0.435
    z0 = -half_z
    z1 = half_z

    p000 = (-half_x, -half_y, z0)
    p100 = (half_x, -half_y, z0)
    p110 = (half_x, half_y, z0)
    p010 = (-half_x, half_y, z0)
    p001 = (-half_x, -half_y, z1)
    p101 = (half_x, -half_y, z1)
    p111 = (half_x, half_y, z1)
    p011 = (-half_x, half_y, z1)

    out: List[float] = []

    def _line(a, b) -> None:
        out.extend([a[0], a[1], a[2], b[0], b[1], b[2]])

    # Cube edges.
    _line(p000, p100)
    _line(p100, p110)
    _line(p110, p010)
    _line(p010, p000)

    _line(p001, p101)
    _line(p101, p111)
    _line(p111, p011)
    _line(p011, p001)

    _line(p000, p001)
    _line(p100, p101)
    _line(p110, p111)
    _line(p010, p011)

    # Top body: slimmer and slightly shorter than the main block.
    top_half_x = half_x * 0.62
    top_half_y = half_y * 0.24
    top_half_z = half_z * 0.82
    top_gap_y = half_y * 0.04
    top_cy = half_y + top_gap_y + top_half_y
    tx0 = -top_half_x
    tx1 = top_half_x
    ty0 = top_cy - top_half_y
    ty1 = top_cy + top_half_y
    tz0 = -top_half_z
    tz1 = top_half_z

    t000 = (tx0, ty0, tz0)
    t100 = (tx1, ty0, tz0)
    t110 = (tx1, ty1, tz0)
    t010 = (tx0, ty1, tz0)
    t001 = (tx0, ty0, tz1)
    t101 = (tx1, ty0, tz1)
    t111 = (tx1, ty1, tz1)
    t011 = (tx0, ty1, tz1)

    _line(t000, t100)
    _line(t100, t110)
    _line(t110, t010)
    _line(t010, t000)

    _line(t001, t101)
    _line(t101, t111)
    _line(t111, t011)
    _line(t011, t001)

    _line(t000, t001)
    _line(t100, t101)
    _line(t110, t111)
    _line(t010, t011)

    # Lens: tapered tube (frustum) on the opposite Z face.
    lens_r_wide = 0.22
    lens_r_narrow = 0.09
    lens_h = 0.325
    # Keep the narrow end flush against the camera body face.
    gap = 0.0
    base_z = z0 - lens_h - gap
    top_z = base_z + lens_h
    ring_wide = []
    ring_narrow = []
    for i in range(segs):
        ang = (2.0 * math.pi * i) / float(segs)
        ca = math.cos(ang)
        sa = math.sin(ang)
        ring_wide.append((lens_r_wide * ca, lens_r_wide * sa, base_z))
        ring_narrow.append((lens_r_narrow * ca, lens_r_narrow * sa, top_z))

    for i in range(segs):
        aw = ring_wide[i]
        bw = ring_wide[(i + 1) % segs]
        an = ring_narrow[i]
        bn = ring_narrow[(i + 1) % segs]
        _line(aw, bw)
        _line(an, bn)
        _line(aw, an)

    return out
