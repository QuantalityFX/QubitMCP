"""CUDA counterparts of cloth.surface_contacts; all queries stay on device."""
import warp as wp

from .cloth_kernels import closest_triangle, contact_friction, inward_sweep

# This forward-only solver does not need adjoints. Keep feature and root-search
# loops out of the unroller so first-use compilation does not stall playback.
wp.set_module_options({"enable_backward": False, "max_unroll": 4})


@wp.func
def barycentric(point: wp.vec3, a: wp.vec3, b: wp.vec3, c: wp.vec3):
    ab, ac, ap = b - a, c - a, point - a
    aa, bb, cc = wp.dot(ab, ab), wp.dot(ab, ac), wp.dot(ac, ac)
    denominator = wp.max(aa * cc - bb * bb, 1.0e-30)
    u = (cc * wp.dot(ap, ab) - bb * wp.dot(ap, ac)) / denominator
    v = (aa * wp.dot(ap, ac) - bb * wp.dot(ap, ab)) / denominator
    w = wp.vec3(wp.max(1.0 - u - v, 0.0), wp.max(u, 0.0), wp.max(v, 0.0))
    return w / wp.max(w[0] + w[1] + w[2], 1.0e-30)


@wp.func
def closest_segments(a: wp.vec3, b: wp.vec3, c: wp.vec3, d: wp.vec3):
    ab, cd, ac = b - a, d - c, a - c
    aa, bb, cc = wp.dot(ab, ab), wp.dot(ab, cd), wp.dot(cd, cd)
    dd, ee = wp.dot(ab, ac), wp.dot(cd, ac)
    denominator = wp.length_sq(wp.cross(ab, cd))
    s = float(0.0)
    if denominator > 1.0e-24:
        s = wp.clamp((bb * ee - cc * dd) / denominator, 0.0, 1.0)
    t = (bb * s + ee) / wp.max(cc, 1.0e-30)
    if t < 0.0:
        s = wp.clamp(-dd / wp.max(aa, 1.0e-30), 0.0, 1.0)
    if t > 1.0:
        s = wp.clamp((bb - dd) / wp.max(aa, 1.0e-30), 0.0, 1.0)
    t = wp.clamp(t, 0.0, 1.0)
    return s, t, a + ab * s, c + cd * t


@wp.func
def edge_contact(oa: wp.vec3, ob: wp.vec3, a: wp.vec3, b: wp.vec3,
                 oc: wp.vec3, od: wp.vec3, c: wp.vec3, d: wp.vec3, thickness: float):
    ab, cd, old_ab, old_cd = b - a, d - c, ob - oa, od - oc
    cross, old_cross = wp.cross(ab, cd), wp.cross(old_ab, old_cd)
    length, old_length = wp.length(cross), wp.length(old_cross)
    valid = length * length > 1.0e-8 * wp.length_sq(ab) * wp.length_sq(cd)
    valid = valid and old_length * old_length > 1.0e-8 * wp.length_sq(old_ab) * wp.length_sq(old_cd)
    valid = valid and length > 1.0e-12 and old_length > 1.0e-12
    normal = cross / wp.max(length, 1.0e-30)
    old_normal = old_cross / wp.max(old_length, 1.0e-30)
    old_signed = wp.dot(oa - oc, old_normal)
    side = float(1.0)
    if old_signed < 0.0:
        side = -1.0
    normal *= side
    signed = wp.dot(a - c, normal)
    s, t, p, q = closest_segments(a, b, c, d)
    active = s > 1.0e-6 and s < 1.0 - 1.0e-6 and t > 1.0e-6 and t < 1.0 - 1.0e-6 and wp.length(p - q) < thickness
    if wp.abs(old_signed) > 1.0e-7 and signed < 0.0 and valid:
        old_distance = wp.abs(old_signed)
        fraction = old_distance / wp.max(old_distance - signed, 1.0e-30)
        if wp.length_sq(ab - old_ab) + wp.length_sq(cd - old_cd) > 1.0e-12:
            lo, hi = float(0.0), float(1.0)
            for step in range(16):
                mid = (lo + hi) * 0.5
                ma, mb, mc, md = wp.lerp(oa, a, mid), wp.lerp(ob, b, mid), wp.lerp(oc, c, mid), wp.lerp(od, d, mid)
                if wp.dot(ma - mc, wp.cross(mb - ma, md - mc)) * side > 0.0:
                    lo = mid
                else:
                    hi = mid
            fraction = (lo + hi) * 0.5
        ha, hb, hc, hd = wp.lerp(oa, a, fraction), wp.lerp(ob, b, fraction), wp.lerp(oc, c, fraction), wp.lerp(od, d, fraction)
        hs, ht, hp, hq = closest_segments(ha, hb, hc, hd)
        if hs > 1.0e-6 and hs < 1.0 - 1.0e-6 and ht > 1.0e-6 and ht < 1.0 - 1.0e-6 and wp.length(hp - hq) < 1.0e-5:
            active = True
            s = hs
            t = ht
    depth = float(0.0)
    if active and valid and wp.abs(old_signed) > 1.0e-7:
        depth = wp.max(thickness - signed, 0.0)
    return s, t, normal, depth


@wp.func
def vertex_face_contact(point: wp.vec3, before: wp.vec3, a: wp.vec3, b: wp.vec3, c: wp.vec3,
                        oa: wp.vec3, ob: wp.vec3, oc: wp.vec3, thickness: float):
    nearest, normal, inside = closest_triangle(point, a, b, c, 0.0)
    old_cross = wp.cross(ob - oa, oc - oa)
    old_normal = old_cross / wp.max(wp.length(old_cross), 1.0e-30)
    old_signed = wp.dot(before - oa, old_normal)
    side = float(1.0)
    if old_signed < 0.0:
        side = -1.0
    normal *= side
    signed = wp.dot(point - nearest, normal)
    time = inward_sweep(before, point, oa, ob, oc, a, b, c, wp.abs(old_signed), signed, side)
    swept = time <= 1.0
    weights = barycentric(nearest, a, b, c)
    if swept:
        weights = barycentric(wp.lerp(before, point, time), wp.lerp(oa, a, time), wp.lerp(ob, b, time), wp.lerp(oc, c, time))
    active = swept or (inside and wp.length(point - nearest) < thickness)
    depth = float(0.0)
    if active and wp.min(weights[0], wp.min(weights[1], weights[2])) > 1.0e-6 and wp.abs(old_signed) > 1.0e-7:
        depth = wp.max(thickness - signed, 0.0)
    return weights, -normal, depth


@wp.kernel
def surface_contact(x: wp.array(dtype=wp.vec3), old: wp.array(dtype=wp.vec3),
                    mass: wp.array(dtype=float), triangles: wp.array2d(dtype=int),
                    surface: wp.array(dtype=wp.vec3), previous: wp.array(dtype=wp.vec3),
                    faces: wp.array2d(dtype=int), bvh: wp.uint64, thickness: float, friction: float, two_sided: int,
                    correction: wp.array(dtype=wp.vec3), counts: wp.array(dtype=float)):
    row = wp.tid()
    ids = wp.vec3i(triangles[row, 0], triangles[row, 1], triangles[row, 2])
    a, b, c = x[ids[0]], x[ids[1]], x[ids[2]]
    oa, ob, oc = old[ids[0]], old[ids[1]], old[ids[2]]
    masses = wp.vec3(mass[ids[0]], mass[ids[1]], mass[ids[2]])
    if masses[0] + masses[1] + masses[2] > 0.0:
        lower = wp.min(wp.min(a, wp.min(b, c)), wp.min(oa, wp.min(ob, oc))) - wp.vec3(thickness)
        upper = wp.max(wp.max(a, wp.max(b, c)), wp.max(oa, wp.max(ob, oc))) + wp.vec3(thickness)
        query = wp.bvh_query_aabb(bvh, lower, upper)
        face = int(0)
        best_depth = float(0.0)
        best_key = int(2147483647)
        chosen_weights, chosen_normal = wp.vec3(0.0), wp.vec3(0.0)
        chosen_motion = wp.vec3(0.0)
        while wp.bvh_query_next(query, face):
            body_a = previous[faces[face, 0]]
            body_cross = wp.cross(previous[faces[face, 1]] - body_a, previous[faces[face, 2]] - body_a)
            body_normal = body_cross / wp.max(wp.length(body_cross), 1.0e-30)
            for feature in range(12):
                weights, normal, depth = wp.vec3(0.0), wp.vec3(0.0), float(0.0)
                motion = wp.vec3(0.0)
                if feature < 3:
                    vertex = int(faces[face, feature])
                    weights, normal, depth = vertex_face_contact(surface[vertex], previous[vertex], a, b, c, oa, ob, oc, thickness)
                    motion = surface[vertex] - previous[vertex]
                else:
                    i, k = (feature - 3) // 3, (feature - 3) % 3
                    j, l = (i + 1) % 3, (k + 1) % 3
                    e, f = ids[i], ids[j]
                    g, h = int(faces[face, k]), int(faces[face, l])
                    s, t, normal, depth = edge_contact(old[e], old[f], x[e], x[f], previous[g], previous[h], surface[g], surface[h], thickness)
                    weights[i] = 1.0 - s
                    weights[j] = s
                    motion = (surface[g] - previous[g]) * (1.0 - t) + (surface[h] - previous[h]) * t
                denominator = wp.dot(masses, wp.cw_mul(weights, weights))
                key = face * 12 + feature
                outside = wp.dot(oa * weights[0] + ob * weights[1] + oc * weights[2] - body_a, body_normal) >= -1.0e-9
                if depth > 1.0e-9 and denominator > 1.0e-12 and (two_sided == 1 or outside):
                    if depth > best_depth + 1.0e-7 or (wp.abs(depth - best_depth) <= 1.0e-7 and key < best_key):
                        best_depth, best_key = depth, key
                        chosen_weights, chosen_normal = weights, normal
                        chosen_motion = motion
        if best_depth > 0.0:
            denominator = wp.dot(masses, wp.cw_mul(chosen_weights, chosen_weights))
            weighted_mass = wp.cw_mul(masses, chosen_weights)
            denominator = wp.max(denominator, 0.5 * wp.max(weighted_mass[0], wp.max(weighted_mass[1], weighted_mass[2])))
            displacement = chosen_normal * best_depth
            relative = (a - oa) * chosen_weights[0] + (b - ob) * chosen_weights[1] + (c - oc) * chosen_weights[2] + displacement - chosen_motion
            displacement -= contact_friction(relative, chosen_normal, best_depth, friction)
            displacement /= denominator
            for column in range(3):
                weight = masses[column] * chosen_weights[column]
                if weight > 1.0e-12:
                    wp.atomic_add(correction, ids[column], displacement * weight)
                    wp.atomic_add(counts, ids[column], 1.0)
