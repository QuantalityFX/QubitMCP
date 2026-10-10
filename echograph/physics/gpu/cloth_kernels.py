"""GPU integration, interaction and contact kernels for the cloth solver."""
import warp as wp


@wp.kernel
def integrate(x: wp.array(dtype=wp.vec3), velocity: wp.array(dtype=wp.vec3),
              old: wp.array(dtype=wp.vec3), rest: wp.array(dtype=wp.vec3),
              mass: wp.array(dtype=float), clock: wp.array(dtype=float),
              gravity: float, damping: float, seam_seconds: float,
              progress: wp.array(dtype=float)):
    i = wp.tid()
    h = clock[0]
    old[i] = x[i]
    if mass[i] == 0.0:
        x[i] = rest[i]
        velocity[i] = wp.vec3(0.0)
    else:
        velocity[i] = (velocity[i] + wp.vec3(0.0, gravity * h, 0.0)) * wp.exp(-damping * h)
        x[i] += velocity[i] * h
    if i == 0:
        progress[0] = wp.min(1.0, (clock[1] + h) / wp.max(seam_seconds, h))


@wp.kernel
def finish_substep(x: wp.array(dtype=wp.vec3), old: wp.array(dtype=wp.vec3),
                   velocity: wp.array(dtype=wp.vec3), mass: wp.array(dtype=float),
                   clock: wp.array(dtype=float)):
    i = wp.tid()
    velocity[i] = (x[i] - old[i]) / clock[0]
    if mass[i] == 0.0:
        velocity[i] = wp.vec3(0.0)
    if i == 0:
        clock[1] += clock[0]


@wp.kernel
def grab(x: wp.array(dtype=wp.vec3), mass: wp.array(dtype=float),
         ids: wp.array(dtype=int), weights: wp.array(dtype=float),
         target: wp.array(dtype=wp.vec3), count: wp.array(dtype=int),
         clock: wp.array(dtype=float)):
    value = wp.vec3(0.0)
    denominator = 1.0 / (50000.0 * clock[0] * clock[0])
    for j in range(count[0]):
        w = weights[j]
        value += w * x[ids[j]]
        denominator += mass[ids[j]] * w * w
    correction = (target[0] - value) / denominator
    for j in range(count[0]):
        x[ids[j]] += mass[ids[j]] * weights[j] * correction


@wp.kernel
def ground_contact(x: wp.array(dtype=wp.vec3), old: wp.array(dtype=wp.vec3),
                   mass: wp.array(dtype=float), height: float, friction: float):
    i = wp.tid()
    p = x[i]
    if mass[i] > 0.0 and p[1] < height:
        p[1] = height
        p[0] -= (p[0] - old[i][0]) * friction
        p[2] -= (p[2] - old[i][2]) * friction
        x[i] = p


@wp.kernel
def sample_collider(start: wp.array(dtype=wp.vec3), end: wp.array(dtype=wp.vec3),
                    current: wp.array(dtype=wp.vec3), previous: wp.array(dtype=wp.vec3),
                    fraction: float, previous_fraction: float):
    i = wp.tid()
    current[i] = wp.lerp(start[i], end[i], fraction)
    previous[i] = wp.lerp(start[i], end[i], previous_fraction)


@wp.kernel
def triangle_bounds(x: wp.array(dtype=wp.vec3), previous: wp.array(dtype=wp.vec3),
                    faces: wp.array2d(dtype=int), lower: wp.array(dtype=wp.vec3),
                    upper: wp.array(dtype=wp.vec3), swept: int):
    face = wp.tid()
    a, b, c = faces[face, 0], faces[face, 1], faces[face, 2]
    lo = wp.min(x[a], wp.min(x[b], x[c]))
    hi = wp.max(x[a], wp.max(x[b], x[c]))
    if swept == 1:
        lo = wp.min(lo, wp.min(previous[a], wp.min(previous[b], previous[c])))
        hi = wp.max(hi, wp.max(previous[a], wp.max(previous[b], previous[c])))
    lower[face] = lo
    upper[face] = hi


@wp.func
def closest_triangle(p: wp.vec3, a: wp.vec3, b: wp.vec3, c: wp.vec3, inside_tolerance: float):
    ab, ac = b - a, c - a
    cross = wp.cross(ab, ac)
    norm2 = wp.dot(cross, cross)
    normal = cross / wp.sqrt(wp.max(norm2, 1.0e-30))
    projected = p - cross * (wp.dot(p - a, cross) / wp.max(norm2, 1.0e-30))
    aa, bb, cc = wp.dot(ab, ab), wp.dot(ab, ac), wp.dot(ac, ac)
    ap = projected - a
    denominator = wp.max(aa * cc - bb * bb, 1.0e-30)
    u = (cc * wp.dot(ap, ab) - bb * wp.dot(ap, ac)) / denominator
    v = (aa * wp.dot(ap, ac) - bb * wp.dot(ap, ab)) / denominator
    inside = u >= -inside_tolerance and v >= -inside_tolerance and u + v <= 1.0 + inside_tolerance and norm2 > 1.0e-24
    # Construct from the face instead of subtracting a large swept distance from
    # p. Float32 cancellation otherwise leaves a resolved contact slightly inside
    # its margin and applies friction again on the following contact pass.
    nearest = a + ab * u + ac * v
    if not inside:
        nearest = a + ab * wp.clamp(wp.dot(p - a, ab) / wp.max(aa, 1.0e-30), 0.0, 1.0)
        bc = c - b
        candidate = b + bc * wp.clamp(wp.dot(p - b, bc) / wp.max(wp.dot(bc, bc), 1.0e-30), 0.0, 1.0)
        if wp.length_sq(p - candidate) < wp.length_sq(p - nearest):
            nearest = candidate
        candidate = a + ac * wp.clamp(wp.dot(p - a, ac) / wp.max(cc, 1.0e-30), 0.0, 1.0)
        if wp.length_sq(p - candidate) < wp.length_sq(p - nearest):
            nearest = candidate
    return nearest, normal, inside


@wp.func
def inward_sweep(before: wp.vec3, point: wp.vec3, previous_a: wp.vec3, previous_b: wp.vec3,
                 previous_c: wp.vec3, a: wp.vec3, b: wp.vec3, c: wp.vec3,
                 old_signed: float, signed: float):
    result = bool(False)
    if old_signed >= 0.0 and signed < 0.0:
        fraction = old_signed / wp.max(old_signed - signed, 1.0e-30)
        ab_change = b - a - (previous_b - previous_a)
        ac_change = c - a - (previous_c - previous_a)
        if wp.length_sq(ab_change) + wp.length_sq(ac_change) > 1.0e-12:
            lo, hi = float(0.0), float(1.0)
            for step in range(16):
                mid = (lo + hi) * 0.5
                ma, mb, mc = wp.lerp(previous_a, a, mid), wp.lerp(previous_b, b, mid), wp.lerp(previous_c, c, mid)
                mp = wp.lerp(before, point, mid)
                if wp.dot(mp - ma, wp.cross(mb - ma, mc - ma)) > 0.0:
                    lo = mid
                else:
                    hi = mid
            fraction = (lo + hi) * 0.5
        hit = wp.lerp(before, point, fraction)
        ha, hb, hc = wp.lerp(previous_a, a, fraction), wp.lerp(previous_b, b, fraction), wp.lerp(previous_c, c, fraction)
        nearest, normal, inside = closest_triangle(hit, ha, hb, hc, 1.0e-6)
        result = inside
    return result


@wp.kernel
def mesh_contact(x: wp.array(dtype=wp.vec3), old: wp.array(dtype=wp.vec3),
                 mass: wp.array(dtype=float), surface: wp.array(dtype=wp.vec3),
                 previous: wp.array(dtype=wp.vec3), faces: wp.array2d(dtype=int),
                 bvh: wp.uint64, thickness: float, margin: float, friction: float):
    i = wp.tid()
    if mass[i] > 0.0:
        p, before = x[i], old[i]
        pad = wp.vec3(margin)
        query = wp.bvh_query_aabb(bvh, wp.min(p, before) - pad, wp.max(p, before) + pad)
        face = int(0)
        deepest = float(-1.0)
        chosen = p
        chosen_direction = wp.vec3(0.0)
        motion = wp.vec3(0.0)
        while wp.bvh_query_next(query, face):
            a, b, c = faces[face, 0], faces[face, 1], faces[face, 2]
            nearest, normal, inside = closest_triangle(p, surface[a], surface[b], surface[c], 0.0)
            delta = p - nearest
            distance = wp.length(delta)
            signed = wp.dot(delta, normal)
            old_cross = wp.cross(previous[b] - previous[a], previous[c] - previous[a])
            old_normal = old_cross / wp.max(wp.length(old_cross), 1.0e-30)
            old_signed = wp.dot(before - previous[a], old_normal)
            swept = inward_sweep(before, p, previous[a], previous[b], previous[c],
                                 surface[a], surface[b], surface[c], old_signed, signed)
            penetrated = inside and signed < 0.0 and signed > -margin
            if swept or distance < thickness or penetrated:
                direction = delta / wp.max(distance, 1.0e-12)
                if swept or penetrated or distance < 1.0e-10:
                    direction = normal
                candidate = nearest + direction * thickness
                depth = wp.length_sq(candidate - p)
                if depth > deepest:
                    deepest = depth
                    chosen = candidate
                    chosen_direction = direction
                    motion = (surface[a] + surface[b] + surface[c] - previous[a] - previous[b] - previous[c]) / 3.0
        if deepest >= 0.0:
            relative = chosen - before - motion
            tangent = relative - chosen_direction * wp.dot(relative, chosen_direction)
            x[i] = chosen - friction * tangent


@wp.func
def adjacent(vertex: int, other: int, offsets: wp.array(dtype=int), neighbors: wp.array(dtype=int)):
    # Sorted CSR neighbors include authored edges, seam partners and the vertex itself.
    lo, hi = int(offsets[vertex]), int(offsets[vertex + 1])
    end = hi
    while lo < hi:
        mid = (lo + hi) / 2
        if neighbors[mid] < other:
            lo = mid + 1
        else:
            hi = mid
    return lo < end and neighbors[lo] == other


@wp.kernel
def self_face_contact(x: wp.array(dtype=wp.vec3), old: wp.array(dtype=wp.vec3),
                      mass: wp.array(dtype=float), faces: wp.array2d(dtype=int),
                      bvh: wp.uint64, thickness: float, offsets: wp.array(dtype=int),
                      neighbors: wp.array(dtype=int), correction: wp.array(dtype=wp.vec3),
                      counts: wp.array(dtype=float)):
    i = wp.tid()
    p = x[i]
    pad = wp.vec3(thickness)
    query = wp.bvh_query_aabb(bvh, p - pad, p + pad)
    face = int(0)
    while wp.bvh_query_next(query, face):
        a, b, c = faces[face, 0], faces[face, 1], faces[face, 2]
        if not adjacent(i, a, offsets, neighbors) and not adjacent(i, b, offsets, neighbors) and not adjacent(i, c, offsets, neighbors):
            nearest, normal, inside = closest_triangle(p, x[a], x[b], x[c], 0.0)
            delta = p - nearest
            distance = wp.length(delta)
            if distance < thickness:
                direction = delta / wp.max(distance, 1.0e-12)
                if distance < 1.0e-10:
                    direction = normal
                    if wp.dot(old[i] - (old[a] + old[b] + old[c]) / 3.0, normal) < 0.0:
                        direction = -normal
                ab, ac, ap = x[b] - x[a], x[c] - x[a], nearest - x[a]
                aa, bb, cc = wp.dot(ab, ab), wp.dot(ab, ac), wp.dot(ac, ac)
                denominator = wp.max(aa * cc - bb * bb, 1.0e-30)
                u = (cc * wp.dot(ap, ab) - bb * wp.dot(ap, ac)) / denominator
                v = (aa * wp.dot(ap, ac) - bb * wp.dot(ap, ab)) / denominator
                bary = wp.vec3(wp.clamp(1.0 - u - v, 0.0, 1.0), wp.clamp(u, 0.0, 1.0), wp.clamp(v, 0.0, 1.0))
                bary /= wp.max(bary[0] + bary[1] + bary[2], 1.0e-12)
                effective_mass = mass[i] + bary[0]*bary[0]*mass[a] + bary[1]*bary[1]*mass[b] + bary[2]*bary[2]*mass[c]
                amount = (thickness - distance) / wp.max(effective_mass, 1.0e-12)
                wp.atomic_add(correction, i, direction * (amount * mass[i]))
                wp.atomic_add(counts, i, 1.0)
                for corner in range(3):
                    index = faces[face, corner]
                    wp.atomic_add(correction, index, -direction * (amount * bary[corner] * mass[index]))
                    # Float32 barycentric noise at shared edges must not count an
                    # uninvolved corner and halve that vertex's other corrections.
                    if bary[corner] > 1.0e-6:
                        wp.atomic_add(counts, index, 1.0)


@wp.kernel
def apply_contacts(x: wp.array(dtype=wp.vec3), correction: wp.array(dtype=wp.vec3),
                   counts: wp.array(dtype=float)):
    i = wp.tid()
    x[i] += correction[i] / wp.max(counts[i], 1.0)


@wp.kernel
def self_point_contact(x: wp.array(dtype=wp.vec3), mass: wp.array(dtype=float),
                       grid: wp.uint64, radius: float, offsets: wp.array(dtype=int),
                       neighbors: wp.array(dtype=int), correction: wp.array(dtype=wp.vec3),
                       counts: wp.array(dtype=float)):
    i = wp.tid()
    for j in wp.hash_grid_query(grid, x[i], radius):
        if j > i and not adjacent(i, j, offsets, neighbors):
            delta = x[i] - x[j]
            distance = wp.length(delta)
            if distance <= radius:
                direction = delta / wp.max(distance, 1.0e-12)
                if distance < 1.0e-12:
                    direction = wp.vec3(0.0, 0.0, 1.0)
                amount = (radius - distance) / wp.max(mass[i] + mass[j], 1.0e-12)
                wp.atomic_add(correction, i, direction * (amount * mass[i]))
                wp.atomic_add(correction, j, -direction * (amount * mass[j]))
                wp.atomic_add(counts, i, 1.0)
                wp.atomic_add(counts, j, 1.0)
