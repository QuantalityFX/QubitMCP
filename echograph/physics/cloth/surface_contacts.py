"""Continuous feature contacts, distributed to existing cloth vertices.

Vertex/body-face contacts live in collisions.py. The complementary body-vertex/
cloth-face and edge/edge tests cover coarse triangles without adding particles.
"""
import numpy as np

from .collisions import closest_points, friction_correction, inward_sweeps


def barycentric(points, triangles):
    ab, ac, ap = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0], points - triangles[:, 0]
    aa, bb, cc = np.sum(ab * ab, axis=1), np.sum(ab * ac, axis=1), np.sum(ac * ac, axis=1)
    denominator = np.maximum(aa * cc - bb * bb, 1e-30)
    u = (cc * np.sum(ap * ab, axis=1) - bb * np.sum(ap * ac, axis=1)) / denominator
    v = (aa * np.sum(ap * ac, axis=1) - bb * np.sum(ap * ab, axis=1)) / denominator
    weights = np.maximum(np.c_[1 - u - v, u, v], 0)
    return weights / np.maximum(weights.sum(axis=1)[:, None], 1e-30)


def closest_segments(a, b, c, d):
    ab, cd, ac = b - a, d - c, a - c
    aa, bb, cc = np.sum(ab * ab, axis=1), np.sum(ab * cd, axis=1), np.sum(cd * cd, axis=1)
    dd, ee = np.sum(ab * ac, axis=1), np.sum(cd * ac, axis=1)
    denominator = np.sum(np.cross(ab, cd)**2, axis=1)
    s = np.where(denominator > 1e-24, np.clip((bb * ee - cc * dd) / np.maximum(denominator, 1e-30), 0, 1), 0)
    t = (bb * s + ee) / np.maximum(cc, 1e-30)
    s = np.where(t < 0, np.clip(-dd / np.maximum(aa, 1e-30), 0, 1), s)
    s = np.where(t > 1, np.clip((bb - dd) / np.maximum(aa, 1e-30), 0, 1), s)
    t = np.clip(t, 0, 1)
    return s, t, a + ab * s[:, None], c + cd * t[:, None]


def edge_contact(start, end, body_start, body_end, thickness):
    """Return edge parameter, direction and depth; zero depth means no contact."""
    ab, cd = end[:, 1] - end[:, 0], body_end[:, 1] - body_end[:, 0]
    old_ab, old_cd = start[:, 1] - start[:, 0], body_start[:, 1] - body_start[:, 0]
    normal, old_normal = np.cross(ab, cd), np.cross(old_ab, old_cd)
    length, old_length = np.linalg.norm(normal, axis=1), np.linalg.norm(old_normal, axis=1)
    # Nearly parallel edges have no reliable separating normal. A projection
    # can align them exactly; float32 roundoff must not invent a sideways kick.
    valid = (length**2 > 1e-8 * np.sum(ab * ab, axis=1) * np.sum(cd * cd, axis=1))
    valid &= (old_length**2 > 1e-8 * np.sum(old_ab * old_ab, axis=1) * np.sum(old_cd * old_cd, axis=1))
    valid &= (length > 1e-12) & (old_length > 1e-12)
    normal /= np.maximum(length[:, None], 1e-30)
    old_normal /= np.maximum(old_length[:, None], 1e-30)
    old_signed = np.sum((start[:, 0] - body_start[:, 0]) * old_normal, axis=1)
    side = np.where(old_signed < 0, -1., 1.)
    normal *= side[:, None]
    signed = np.sum((end[:, 0] - body_end[:, 0]) * normal, axis=1)
    s, t, p, q = closest_segments(end[:, 0], end[:, 1], body_end[:, 0], body_end[:, 1])
    interior = (s > 1e-6) & (s < 1 - 1e-6) & (t > 1e-6) & (t < 1 - 1e-6)
    active = interior & (np.linalg.norm(p - q, axis=1) < thickness)
    crossing = np.flatnonzero((np.abs(old_signed) > 1e-7) & (signed < 0) & valid)
    if len(crossing):
        old_distance = np.abs(old_signed[crossing])
        fraction = old_distance / np.maximum(old_distance - signed[crossing], 1e-30)
        deforming = np.flatnonzero(np.sum((ab[crossing] - old_ab[crossing])**2 +
                                         (cd[crossing] - old_cd[crossing])**2, axis=1) > 1e-12)
        if len(deforming):
            ids = crossing[deforming]
            lo, hi = np.zeros(len(ids)), np.ones(len(ids))
            for _ in range(16):
                mid = (lo + hi) * .5
                a = start[ids] * (1 - mid[:, None, None]) + end[ids] * mid[:, None, None]
                b = body_start[ids] * (1 - mid[:, None, None]) + body_end[ids] * mid[:, None, None]
                positive = np.sum((a[:, 0] - b[:, 0]) * np.cross(a[:, 1] - a[:, 0], b[:, 1] - b[:, 0]), axis=1) * side[ids] > 0
                lo, hi = np.where(positive, mid, lo), np.where(positive, hi, mid)
            fraction[deforming] = (lo + hi) * .5
        a = start[crossing] * (1 - fraction[:, None, None]) + end[crossing] * fraction[:, None, None]
        b = body_start[crossing] * (1 - fraction[:, None, None]) + body_end[crossing] * fraction[:, None, None]
        hs, ht, hp, hq = closest_segments(a[:, 0], a[:, 1], b[:, 0], b[:, 1])
        hit = (hs > 1e-6) & (hs < 1 - 1e-6) & (ht > 1e-6) & (ht < 1 - 1e-6) & (np.linalg.norm(hp - hq, axis=1) < 1e-5)
        ids = crossing[hit]
        active[ids], s[ids], t[ids] = True, hs[hit], ht[hit]
    active &= valid & (np.abs(old_signed) > 1e-7)
    return s, t, normal, np.where(active, np.maximum(thickness - signed, 0), 0)


def vertex_face_contact(point, before, surface, previous, thickness):
    nearest, normal, inside = closest_points(point, surface)
    old_normal = np.cross(previous[:, 1] - previous[:, 0], previous[:, 2] - previous[:, 0])
    old_normal /= np.maximum(np.linalg.norm(old_normal, axis=1)[:, None], 1e-30)
    old_signed = np.sum((before - previous[:, 0]) * old_normal, axis=1)
    side = np.where(old_signed < 0, -1., 1.)
    normal *= side[:, None]
    signed = np.sum((point - nearest) * normal, axis=1)
    times = inward_sweeps(before, point, previous, surface, np.abs(old_signed), signed, side)
    swept = np.isfinite(times)
    weights = barycentric(nearest, surface)
    if np.any(swept):
        fraction = times[swept, None]
        pose = previous[swept] * (1 - fraction[:, :, None]) + surface[swept] * fraction[:, :, None]
        hit = before[swept] * (1 - fraction) + point[swept] * fraction
        weights[swept] = barycentric(hit, pose)
    active = swept | (inside & (np.linalg.norm(point - nearest, axis=1) < thickness))
    # Boundary contacts belong to vertex/face or edge/edge tests. Excluding them
    # also avoids ill-conditioned impulses when only a tiny weight is movable.
    active &= (weights.min(axis=1) > 1e-6) & (np.abs(old_signed) > 1e-7)
    return weights, -normal, np.where(active, np.maximum(thickness - signed, 0), 0)


def project_surface_contacts(collider, x, old, inverse_mass, triangles, thickness, friction=0.0):
    if not len(triangles) or not len(collider.triangles):
        return False
    current, previous = x[triangles], old[triangles]
    centers, old_centers = current.mean(axis=1), previous.mean(axis=1)
    radii = np.maximum(np.linalg.norm(current - centers[:, None], axis=2).max(axis=1),
                       np.linalg.norm(previous - old_centers[:, None], axis=2).max(axis=1))
    broadphase = collider.broadphase()
    rows, faces = broadphase.query_pairs(centers, radii + thickness, old_centers)
    lower = np.minimum(current.min(axis=1), previous.min(axis=1)) - thickness
    upper = np.maximum(current.max(axis=1), previous.max(axis=1)) + thickness
    keep = (inverse_mass[triangles[rows]].sum(axis=1) > 0) & np.all(
        (upper[rows] >= broadphase.lower[faces]) & (lower[rows] <= broadphase.upper[faces]), axis=1)
    rows, faces = rows[keep], faces[keep]
    if not len(rows):
        return False
    a, old_a, b = current[rows], previous[rows], collider._surface[faces]
    old_b = (collider.previous[collider.triangles[faces]] if collider.previous is not None
             and collider.previous.shape == collider.vertices.shape else b)
    depths, weights, directions, motions = [], [], [], []
    cloth_lower, cloth_upper = lower[rows], upper[rows]
    for corner in range(3):
        valid = np.flatnonzero(np.all((np.maximum(b[:, corner], old_b[:, corner]) >= cloth_lower) &
                                     (np.minimum(b[:, corner], old_b[:, corner]) <= cloth_upper), axis=1))
        w, n, depth = np.zeros((len(rows), 3)), np.zeros((len(rows), 3)), np.zeros(len(rows))
        if len(valid):
            w[valid], n[valid], depth[valid] = vertex_face_contact(
                b[valid, corner], old_b[valid, corner], a[valid], old_a[valid], thickness)
        weights.append(w)
        directions.append(n)
        depths.append(depth)
        motions.append(b[:, corner] - old_b[:, corner])
    for i, j in ((0, 1), (1, 2), (2, 0)):
        edge_lower = np.minimum(a[:, [i, j]].min(axis=1), old_a[:, [i, j]].min(axis=1)) - thickness
        edge_upper = np.maximum(a[:, [i, j]].max(axis=1), old_a[:, [i, j]].max(axis=1)) + thickness
        for k, l in ((0, 1), (1, 2), (2, 0)):
            body_lower = np.minimum(b[:, [k, l]].min(axis=1), old_b[:, [k, l]].min(axis=1))
            body_upper = np.maximum(b[:, [k, l]].max(axis=1), old_b[:, [k, l]].max(axis=1))
            valid = np.flatnonzero(np.all((body_upper >= edge_lower) & (body_lower <= edge_upper), axis=1))
            s, t = np.zeros(len(rows)), np.zeros(len(rows))
            n, depth = np.zeros((len(rows), 3)), np.zeros(len(rows))
            if len(valid):
                s[valid], t[valid], n[valid], depth[valid] = edge_contact(
                    old_a[valid][:, [i, j]], a[valid][:, [i, j]], old_b[valid][:, [k, l]], b[valid][:, [k, l]], thickness)
            w = np.zeros((len(rows), 3))
            w[:, i], w[:, j] = 1 - s, s
            weights.append(w)
            directions.append(n)
            depths.append(depth)
            motions.append((b[:, k] - old_b[:, k]) * (1 - t[:, None]) +
                           (b[:, l] - old_b[:, l]) * t[:, None])
    depths = np.stack(depths, axis=1)
    weights = np.stack(weights, axis=1)
    effective_mass = np.sum(inverse_mass[triangles[rows]][:, None] * weights**2, axis=2)
    depths[effective_mass <= 1e-12] = 0
    if not collider.two_sided:
        body_normal = np.cross(old_b[:, 1] - old_b[:, 0], old_b[:, 2] - old_b[:, 0])
        body_normal /= np.maximum(np.linalg.norm(body_normal, axis=1)[:, None], 1e-30)
        old_contact = np.einsum("nfc,ncd->nfd", weights, old_a)
        outside = np.sum((old_contact - old_b[:, :1]) * body_normal[:, None], axis=2) >= -1e-9
        depths[~outside] = 0
    pair, feature = np.nonzero(depths > 1e-9)
    if not len(pair):
        return False
    depth = depths[pair, feature]
    # One contact per cloth triangle, with a deterministic tie-break for CUDA.
    maximum = np.zeros(len(triangles))
    np.maximum.at(maximum, rows[pair], depth)
    eligible = np.flatnonzero(depth >= maximum[rows[pair]] - 1e-7)
    order = eligible[np.lexsort((faces[pair[eligible]] * 12 + feature[eligible], rows[pair[eligible]]))]
    _, first = np.unique(rows[pair[order]], return_index=True)
    chosen = order[first]
    pair, feature, depth = pair[chosen], feature[chosen], depth[chosen]
    ids = triangles[rows[pair]]
    w = weights[pair, feature]
    n = np.stack(directions, axis=1)[pair, feature]
    mass = inverse_mass[ids]
    denominator = np.maximum(np.sum(mass * w * w, axis=1), 1e-30)
    # A contact almost on a pin can give its only free vertex a vanishing
    # barycentric weight. Bound that amplification to two times the contact
    # correction; further passes can converge without launching a mesh spike.
    denominator = np.maximum(denominator, .5 * np.max(mass * w, axis=1))
    displacement = n * depth[:, None]
    if friction > 0:
        motion = np.stack(motions, axis=1)[pair, feature]
        relative = np.sum(w[:, :, None] * (a[pair] - old_a[pair]), axis=1) + displacement - motion
        displacement -= friction_correction(relative, n, depth, friction)
    displacement /= denominator[:, None]
    correction, counts = np.zeros_like(x), np.zeros(len(x))
    for column in range(3):
        np.add.at(correction, ids[:, column], displacement * (mass[:, column] * w[:, column])[:, None])
        np.add.at(counts, ids[:, column], (mass[:, column] * w[:, column] > 1e-12).astype(float))
    x += correction / np.maximum(counts[:, None], 1)
    return True
