"""Triangle contact, swept tests, and friction. No renderer dependencies."""
from dataclasses import dataclass
from itertools import chain
import numpy as np
from scipy.spatial import cKDTree


def friction_correction(relative, normal, normal_depth, friction):
    """Coulomb contact: stick below the normal-load limit, otherwise slide."""
    tangent = relative - normal * np.sum(relative * normal, axis=1)[:, None]
    length = np.linalg.norm(tangent, axis=1)
    limit = friction * np.maximum(normal_depth, 0)
    return tangent * np.minimum(1, limit / np.maximum(length, 1e-30))[:, None]


def surface_motion(points, surface, previous):
    """Motion of the material at each contact, including body deformation."""
    ab, ac = previous[:, 1] - previous[:, 0], previous[:, 2] - previous[:, 0]
    ap = points - previous[:, 0]
    aa, bb, cc = np.sum(ab * ab, axis=1), np.sum(ab * ac, axis=1), np.sum(ac * ac, axis=1)
    denominator = np.maximum(aa * cc - bb * bb, 1e-30)
    u = (cc * np.sum(ap * ab, axis=1) - bb * np.sum(ap * ac, axis=1)) / denominator
    v = (aa * np.sum(ap * ac, axis=1) - bb * np.sum(ap * ab, axis=1)) / denominator
    motion = surface - previous
    return motion[:, 0] + u[:, None] * (motion[:, 1] - motion[:, 0]) + v[:, None] * (motion[:, 2] - motion[:, 0])


def closest_points(points, triangles, inside_tolerance=0.0):
    """Closest surface points for broadcast pairs (including triangle edges)."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    ab, ac = b - a, c - a
    normal = np.cross(ab, ac)
    norm2 = np.sum(normal * normal, axis=1)
    projected = points - normal * (np.sum((points - a) * normal, axis=1) / np.maximum(norm2, 1e-30))[:, None]
    d00, d01, d11 = np.sum(ab * ab, axis=1), np.sum(ab * ac, axis=1), np.sum(ac * ac, axis=1)
    ap = projected - a
    denominator = d00 * d11 - d01 * d01
    u = (d11 * np.sum(ap * ab, axis=1) - d01 * np.sum(ap * ac, axis=1)) / np.maximum(denominator, 1e-30)
    v = (d00 * np.sum(ap * ac, axis=1) - d01 * np.sum(ap * ab, axis=1)) / np.maximum(denominator, 1e-30)
    inside = (u >= -inside_tolerance) & (v >= -inside_tolerance) & (u + v <= 1 + inside_tolerance) & (norm2 > 1e-24)
    candidates = []
    for start, end in ((a, b), (b, c), (c, a)):
        edge = end - start
        t = np.clip(np.sum((points - start) * edge, axis=1) / np.maximum(np.sum(edge * edge, axis=1), 1e-30), 0, 1)
        candidates.append(start + edge * t[:, None])
    candidates = np.stack(candidates, axis=1)
    distance2 = np.sum((candidates - points[:, None]) ** 2, axis=2)
    result = candidates[np.arange(len(points)), np.argmin(distance2, axis=1)]
    result[inside] = projected[inside]
    return result, normal / np.sqrt(np.maximum(norm2, 1e-30))[:, None], inside


def inward_sweeps(old, points, previous, surface, old_signed, signed, side):
    """First bracketed inward plane crossing, verified on the moving triangle."""
    times = np.full(len(points), np.inf)
    crossing = np.flatnonzero((old_signed >= 0) & (signed < 0))
    if not len(crossing):
        return times
    start, end = previous[crossing], surface[crossing]
    fraction = old_signed[crossing] / np.maximum(old_signed[crossing] - signed[crossing], 1e-30)
    edge_change = (end[:, 1:] - end[:, :1]) - (start[:, 1:] - start[:, :1])
    deforming = np.flatnonzero(np.sum(edge_change * edge_change, axis=(1, 2)) > 1e-12)
    if len(deforming):
        lo, hi = np.zeros(len(deforming)), np.ones(len(deforming))
        d = crossing[deforming]
        for _ in range(16):
            mid = (lo + hi) * .5
            pose = start[deforming] * (1 - mid[:, None, None]) + end[deforming] * mid[:, None, None]
            point = old[d] * (1 - mid[:, None]) + points[d] * mid[:, None]
            plane = np.cross(pose[:, 1] - pose[:, 0], pose[:, 2] - pose[:, 0])
            positive = np.sum((point - pose[:, 0]) * plane, axis=1) * side[d] > 0
            lo, hi = np.where(positive, mid, lo), np.where(positive, hi, mid)
        fraction[deforming] = (lo + hi) * .5
    hit = old[crossing] * (1 - fraction[:, None]) + points[crossing] * fraction[:, None]
    pose = start * (1 - fraction[:, None, None]) + end * fraction[:, None, None]
    _, _, inside = closest_points(hit, pose, inside_tolerance=1e-6)
    times[crossing[inside]] = fraction[inside]
    return times


class TriangleBroadphase:
    """Conservative size buckets avoid one large face widening every query."""
    def __init__(self, surface, previous_surface=None):
        envelope = (surface if previous_surface is None else
                    np.concatenate((surface, previous_surface), axis=1))
        self.centers = envelope.mean(axis=1)
        self.radii = np.max(np.linalg.norm(envelope - self.centers[:, None], axis=2), axis=1)
        self.lower, self.upper = envelope.min(axis=1), envelope.max(axis=1)
        self.buckets = []
        levels = np.floor(np.log2(np.maximum(self.radii, 1e-12))).astype(np.int32)
        for level in np.unique(levels):
            faces = np.flatnonzero(levels == level)
            self.buckets.append((faces, cKDTree(self.centers[faces]), float(self.radii[faces].max())))

    def query_pairs(self, points, padding, previous_points=None):
        if not self.buckets or not len(points):
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
        previous = points if previous_points is None else previous_points
        midpoint = (points + previous) * .5
        travel = np.linalg.norm(points - previous, axis=1) * .5
        padding = np.broadcast_to(np.asarray(padding), (len(points),))
        query_lower = np.minimum(points, previous) - padding[:, None]
        query_upper = np.maximum(points, previous) + padding[:, None]
        rows_all, faces_all = [], []
        for faces, tree, radius in self.buckets:
            nearby = tree.query_ball_point(midpoint, radius + travel + padding)
            counts = np.fromiter(map(len, nearby), dtype=np.int64, count=len(points))
            count = int(counts.sum())
            if not count:
                continue
            rows = np.repeat(np.arange(len(points)), counts)
            local = np.fromiter(chain.from_iterable(nearby), dtype=np.int64, count=count)
            ids = faces[local]
            delta = midpoint[rows] - self.centers[ids]
            keep = np.sum(delta * delta, axis=1) <= (self.radii[ids] + travel[rows] + padding[rows])**2
            keep &= np.all((query_upper[rows] >= self.lower[ids]) &
                           (query_lower[rows] <= self.upper[ids]), axis=1)
            rows_all.append(rows[keep])
            faces_all.append(ids[keep])
        if not rows_all:
            return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
        return np.concatenate(rows_all), np.concatenate(faces_all)


@dataclass
class MeshCollider:
    """Oriented body surface: resolve inward crossings and allow outward escape."""
    vertices: np.ndarray
    triangles: np.ndarray
    previous: np.ndarray | None = None
    two_sided: bool = False
    surface_thickness: float = 0.0

    def __post_init__(self):
        self.vertices = np.asarray(self.vertices, dtype=float).reshape(-1, 3)
        self.triangles = np.asarray(self.triangles, dtype=np.int32).reshape(-1, 3)
        if not np.isfinite(self.vertices).all():
            raise ValueError("Collider contains non-finite vertices.")
        if not np.isfinite(self.surface_thickness) or self.surface_thickness < 0:
            raise ValueError("Collider thickness must be finite and non-negative.")
        if np.any(self.triangles < 0) or np.any(self.triangles >= len(self.vertices)):
            raise ValueError("Collider triangle index is out of range.")
        triangles = self.vertices[self.triangles]
        usable = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                         triangles[:, 2] - triangles[:, 0]), axis=1) > 1e-16
        self.triangles = self.triangles[usable]
        self._surface = self.vertices[self.triangles]
        # The CUDA backend builds and refits its own BVH. Construct the SciPy
        # broadphase only when the CPU reference actually needs a projection.
        self._broadphase = None

    def interpolate(self, fraction, previous_fraction=0.0):
        if self.previous is None or self.previous.shape != self.vertices.shape:
            return self
        previous = self.previous * (1 - previous_fraction) + self.vertices * previous_fraction
        return MeshCollider(self.previous * (1 - fraction) + self.vertices * fraction, self.triangles,
                            previous, two_sided=self.two_sided, surface_thickness=self.surface_thickness)

    def broadphase(self):
        if self._broadphase is None:
            previous_surface = (self.previous[self.triangles] if self.previous is not None
                                and self.previous.shape == self.vertices.shape else None)
            self._broadphase = TriangleBroadphase(self._surface, previous_surface)
        return self._broadphase

    def project_surface(self, x, old, inverse_mass, triangles, thickness, friction=0.0):
        from .surface_contacts import project_surface_contacts
        return project_surface_contacts(self, x, old, inverse_mass, triangles, thickness + self.surface_thickness, friction)

    def project(self, x, old, inverse_mass, thickness, friction, quality=2, recovery=None):
        thickness += self.surface_thickness
        if not len(self.triangles):
            return np.zeros(len(x), dtype=bool)
        margin = max(thickness * (2 + quality), 0.02)
        rows, faces = self.broadphase().query_pairs(x, margin, old)
        movable = inverse_mass[rows] > 0
        rows, faces = rows[movable], faces[movable]
        contact = np.zeros(len(x), dtype=bool)
        if not len(rows):
            return contact
        surface = self._surface[faces]
        nearest, normal, inside = closest_points(x[rows], surface)
        delta = x[rows] - nearest
        distance = np.linalg.norm(delta, axis=1)
        signed = np.sum(delta * normal, axis=1)
        previous_surface = surface
        old_normal = normal
        if self.previous is not None and self.previous.shape == self.vertices.shape:
            previous_surface = self.previous[self.triangles[faces]]
            old_normal = np.cross(previous_surface[:, 1] - previous_surface[:, 0],
                                  previous_surface[:, 2] - previous_surface[:, 0])
            old_normal /= np.maximum(np.linalg.norm(old_normal, axis=1)[:, None], 1e-30)
        old_signed = np.sum((old[rows] - previous_surface[:, 0]) * old_normal, axis=1)
        # A garment is a thin surface, not a solid body. Preserve the contact's
        # previous side for crossings in either direction, regardless of winding.
        # Coincident garments have no reliable previous side. Use winding for
        # this tiny tie instead of letting float32 noise swap their layer order.
        side = np.where(old_signed < -1e-7, -1.0, 1.0) if self.two_sided else np.ones(len(rows))
        normal *= side[:, None]
        signed *= side
        old_signed *= side
        # Crossing within the face catches fast cloth motion; small penetration
        # with outward winding also recovers cloth overtaken by a moving body.
        # Body contacts are one-sided. Re-testing an outward escape against the
        # old substep position must not pull a recovered point back into the body.
        hit_time = inward_sweeps(old[rows], x[rows], previous_surface, surface, old_signed, signed, side)
        swept = hit_time <= 1
        close = distance < thickness
        # At a shared edge the closest triangle can have its barycentric point
        # just outside (especially in float32). Its outward normal still defines
        # the exit side; a radial direction here would push deeper into a solid.
        penetrated = (inside | (not self.two_sided)) & (signed < 0) & (distance < margin)
        # First contact wins a sweep. Otherwise use the nearest surface, even
        # when it is inactive: a back face of a thin finger must not eject an
        # already exterior point through the opposite side of that finger.
        order = np.lexsort((faces, distance, rows))
        _, first = np.unique(rows[order], return_index=True)
        chosen = order[first]
        swept_ids = np.flatnonzero(swept)
        sweep_order = swept_ids[np.lexsort((faces[swept_ids], hit_time[swept_ids], rows[swept_ids]))]
        swept_rows, first = np.unique(rows[sweep_order], return_index=True)
        chosen[np.searchsorted(rows[chosen], swept_rows)] = sweep_order[first]
        chosen = chosen[(swept | close | penetrated)[chosen]]
        direction = delta / np.maximum(distance[:, None], 1e-12)
        direction[swept | penetrated | (distance < 1e-10)] = normal[swept | penetrated | (distance < 1e-10)]
        target = nearest + direction * thickness
        r = rows[chosen]
        n = direction[chosen]
        correction = target[chosen] - x[r]
        normal_depth = np.sum(correction * n, axis=1)
        repairing = penetrated[chosen] & (old_signed[chosen] < 0) & ~swept[chosen]
        if recovery is not None:
            recovery[r[repairing]] += correction[repairing]
        x[r] = target[chosen]
        if friction > 0:
            relative = x[r] - old[r]
            if self.previous is not None and self.previous.shape == self.vertices.shape:
                relative -= surface_motion(old[r], surface[chosen], previous_surface[chosen])
            # Untangling an existing overlap is not a physical contact load.
            # Applying friction to it glues cloth to an intruding finger.
            x[r] -= friction_correction(relative, n, np.where(repairing, 0, normal_depth), friction)
        contact[r] = True
        return contact
