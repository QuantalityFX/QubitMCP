"""Screen-space surface occlusion for individual mesh-point picking."""
import numpy as np


def visible_points(points, surfaces, tolerance=2e-5):
    """Compare point NDC depths against projected triangles from visible owners.

    Rectangle selection deliberately does not use this filter.
    """
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    visible = np.ones(len(points), dtype=bool)
    if not len(points):
        return visible
    lo, hi = points[:, :2].min(axis=0), points[:, :2].max(axis=0)
    for surface in surfaces:
        triangles = np.asarray(surface, dtype=float).reshape(-1, 3, 3)
        if not len(triangles):
            continue
        keep = np.all(np.isfinite(triangles), axis=(1, 2))
        keep &= np.all(triangles[:, :, :2].max(axis=1) >= lo, axis=1)
        keep &= np.all(triangles[:, :, :2].min(axis=1) <= hi, axis=1)
        triangles = triangles[keep]
        a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
        ab, ac = b - a, c - a
        denominator = ab[:, 0]*ac[:, 1] - ab[:, 1]*ac[:, 0]
        valid = np.abs(denominator) > 1e-10
        denominator = np.where(valid, denominator, 1.)
        for index in np.flatnonzero(visible):
            ap = points[index] - a
            u = (ap[:, 0]*ac[:, 1] - ap[:, 1]*ac[:, 0])/denominator
            v = (ab[:, 0]*ap[:, 1] - ab[:, 1]*ap[:, 0])/denominator
            inside = valid & (u >= -1e-6) & (v >= -1e-6) & (u+v <= 1+1e-6)
            depth = a[:, 2] + u*ab[:, 2] + v*ac[:, 2]
            visible[index] = not np.any(inside & (depth >= -1) & (depth < points[index, 2]-tolerance))
    return visible
