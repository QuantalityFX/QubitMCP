"""Curve sampling and panel readiness checks, independent of the editor."""
from __future__ import annotations

from dataclasses import dataclass
import math

from .document import Piece, Segment


@dataclass(frozen=True)
class Diagnostic:
    piece_id: str
    message: str
    entity_id: str = ""


def _distance_to_line(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return math.dist(p, a)
    # Distance to the finite chord also catches collinear backtracking curves.
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length**2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def sample_segment(piece: Piece, segment: Segment, tolerance: float = 0.05) -> list[tuple[float, float]]:
    start, end = piece.point(segment.start), piece.point(segment.end)
    a, d = (start.x, start.y), (end.x, end.y)
    if segment.kind == "line":
        return [a, d]
    rows = [a]

    def midpoint(p, q):
        return ((p[0] + q[0]) * 0.5, (p[1] + q[1]) * 0.5)

    def subdivide(p, q, r, s, depth):
        if depth >= 14 or max(_distance_to_line(q, p, s), _distance_to_line(r, p, s)) <= tolerance:
            rows.append(s)
            return
        pq, qr, rs = midpoint(p, q), midpoint(q, r), midpoint(r, s)
        pqr, qrs = midpoint(pq, qr), midpoint(qr, rs)
        center = midpoint(pqr, qrs)
        subdivide(p, pq, pqr, center, depth + 1)
        subdivide(center, qrs, rs, s, depth + 1)

    subdivide(a, segment.controls[0], segment.controls[1], d, 0)
    return rows


def edge_length(piece: Piece, segment: Segment) -> float:
    points = sample_segment(piece, segment)
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def outline(piece: Piece, tolerance: float = 0.05):
    rows = []
    for segment in piece.segments:
        samples = sample_segment(piece, segment, tolerance)
        rows.extend(samples if not rows else samples[1:])
    return rows


def bounds(piece: Piece):
    rows = [position for part in piece.boundary_parts() for position in outline(part)] or [(p.x, p.y) for p in piece.points] or [(0.0, 0.0)]
    return min(p[0] for p in rows), min(p[1] for p in rows), max(p[0] for p in rows), max(p[1] for p in rows)


def signed_area(piece: Piece):
    if piece.contours:
        return sum(signed_area(part) for part in piece.boundary_parts())
    rows = outline(piece)
    if len(rows) < 3:
        return 0.0
    origin_x, origin_y = rows[0]
    relative = [(x - origin_x, y - origin_y) for x, y in rows]
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(relative, relative[1:] + relative[:1]))


def _intersects(a, b, c, d, epsilon):
    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p, q, r):
        return (min(p[0], q[0]) - epsilon <= r[0] <= max(p[0], q[0]) + epsilon
                and min(p[1], q[1]) - epsilon <= r[1] <= max(p[1], q[1]) + epsilon)

    cross_eps = epsilon * max(math.dist(a, b), math.dist(c, d), 1.0)
    ab_c, ab_d, cd_a, cd_b = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    if ((ab_c > cross_eps and ab_d < -cross_eps or ab_c < -cross_eps and ab_d > cross_eps)
            and (cd_a > cross_eps and cd_b < -cross_eps or cd_a < -cross_eps and cd_b > cross_eps)):
        return True
    return (abs(ab_c) <= cross_eps and on_segment(a, b, c)
            or abs(ab_d) <= cross_eps and on_segment(a, b, d)
            or abs(cd_a) <= cross_eps and on_segment(c, d, a)
            or abs(cd_b) <= cross_eps and on_segment(c, d, b))


def validate_piece(piece: Piece) -> list[Diagnostic]:
    if piece.contours:
        return [problem for part in piece.boundary_parts() for problem in validate_piece(part)]
    problems = []

    def issue(message, entity=""):
        problems.append(Diagnostic(piece.id, message, entity))

    closed = (piece.closed and bool(piece.segments)
              and piece.segments[-1].end == piece.segments[0].start)
    if not closed:
        issue("Open outline: close the boundary before using this panel.")
    if len(piece.points) < 3 or len(piece.segments) < 2:
        issue("A panel needs at least three points and a closed boundary.")
    rows = outline(piece)
    extent = max((max(p[axis] for p in rows) - min(p[axis] for p in rows) for axis in (0, 1)), default=1.0) if rows else 1.0
    epsilon = max(1e-7, extent * 1e-10)
    for edge in piece.segments:
        if edge_length(piece, edge) <= epsilon:
            issue("Zero-length edge.", edge.id)
    if closed and abs(signed_area(piece)) <= epsilon**2:
        issue("The panel has no usable area.")
    segments = list(zip(rows, rows[1:]))
    # Sweep by minimum X to avoid testing every pair for ordinary panel shapes.
    order = sorted(range(len(segments)), key=lambda i: min(segments[i][0][0], segments[i][1][0]))
    active = []
    for index in order:
        a, b = segments[index]
        low_x = min(a[0], b[0])
        active = [j for j in active if max(segments[j][0][0], segments[j][1][0]) >= low_x - epsilon]
        for other in active:
            c, d = segments[other]
            if abs(index - other) == 1 or closed and {index, other} == {0, len(segments) - 1}:
                # Adjacent edges may meet at an endpoint, but not double back.
                first, second = sorted((index, other))
                if second == first + 1:
                    u, v = segments[first][0], segments[first][1]
                    w = segments[second][1]
                else:
                    u, v, w = segments[-1][0], segments[-1][1], segments[0][1]
                if (_distance_to_line(w, u, v) <= epsilon and math.dist(v, w) > epsilon
                        or _distance_to_line(u, v, w) <= epsilon and math.dist(u, v) > epsilon):
                    issue("Overlapping boundary edges.")
                    return problems
                continue
            if (max(a[1], b[1]) < min(c[1], d[1]) - epsilon
                    or max(c[1], d[1]) < min(a[1], b[1]) - epsilon):
                continue
            if _intersects(a, b, c, d, epsilon):
                issue("Self-intersecting boundary.")
                return problems
        active.append(index)
    return problems
