"""Shared deterministic builders and transactional edits for manual and future agent use."""
from __future__ import annotations

from copy import deepcopy
import math

from .document import PatternDocument, PatternError, Piece, Point, Segment, new_id, validate_integrity
from .geometry import bounds, signed_area


def polygon(name: str, coordinates, *, closed: bool = True) -> Piece:
    points = [Point(new_id(), float(x), float(y)) for x, y in coordinates]
    edges = [Segment(new_id(), a.id, b.id) for a, b in zip(points, points[1:])]
    if closed and len(points) > 1:
        edges.append(Segment(new_id(), points[-1].id, points[0].id))
    piece = Piece(new_id(), name, new_id(), points, edges, closed)
    if closed and signed_area(piece) < 0:
        reverse_boundary(piece)
    return piece


def rectangle(name: str, width: float = 300, height: float = 400, *, top_width: float | None = None) -> Piece:
    top_width = width if top_width is None else top_width
    if not all(math.isfinite(v) and v > 0 for v in (width, height, top_width)):
        raise PatternError("Template dimensions must be positive, finite millimeters.")
    inset = (width - top_width) / 2
    piece = polygon(name, [(0, 0), (width, 0), (width - inset, height), (inset, height)])
    piece.metadata["template"] = {"kind": "rectangle" if width == top_width else "trapezoid",
                                  "width": width, "height": height, "top_width": top_width}
    return piece


def reverse_boundary(piece: Piece) -> None:
    if piece.contours:
        piece.contours = [list(reversed(run)) for run in reversed(piece.contours)]
    piece.segments.reverse()
    for segment in piece.segments:
        segment.start, segment.end = segment.end, segment.start
        segment.controls.reverse()


def duplicate_piece(piece: Piece, *, mirror: bool = False) -> Piece:
    copy = deepcopy(piece)
    copy.id, copy.boundary_id = new_id(), new_id()
    copy.name += " mirrored" if mirror else " copy"
    remap = {p.id: new_id() for p in copy.points}
    edge_remap = {}
    for point in copy.points:
        point.id = remap[point.id]
    for segment in copy.segments:
        old_id = segment.id
        segment.id = edge_remap[old_id] = new_id()
        segment.start, segment.end = remap[segment.start], remap[segment.end]
    copy.contours = [[edge_remap[key] for key in run] for run in copy.contours]
    x0, _, x1, _ = bounds(piece)
    copy.layout[0] += (x1 - x0) + 40
    if mirror:
        for point in copy.points:
            point.x = x0 + x1 - point.x
        for segment in copy.segments:
            for control in segment.controls:
                control[0] = x0 + x1 - control[0]
        reverse_boundary(copy)
        copy.metadata.pop("template", None)
    return copy


def move_point(piece: Piece, point_id: str, x: float, y: float) -> None:
    point = piece.point(point_id)
    dx, dy = x - point.x, y - point.y
    point.x, point.y = x, y
    for segment in piece.segments:
        if segment.kind == "cubic":
            if segment.start == point_id:
                segment.controls[0][0] += dx
                segment.controls[0][1] += dy
            if segment.end == point_id:
                segment.controls[1][0] += dx
                segment.controls[1][1] += dy
    piece.metadata.pop("template", None)


def set_edge_kind(piece: Piece, segment_id: str, kind: str) -> None:
    segment = piece.segment(segment_id)
    if segment.kind == kind:
        return
    if kind not in ("line", "cubic"):
        raise PatternError("Unsupported edge type.")
    segment.kind = kind
    a, b = piece.point(segment.start), piece.point(segment.end)
    segment.controls = ([[a.x + (b.x - a.x) / 3, a.y + (b.y - a.y) / 3],
                         [a.x + (b.x - a.x) * 2 / 3, a.y + (b.y - a.y) * 2 / 3]]
                        if kind == "cubic" else [])
    piece.metadata.pop("template", None)


def translate_edges(document: PatternDocument, selection, dx: float, dy: float) -> None:
    """Translate edge endpoints once each, using a common sheet-space offset."""
    if not all(math.isfinite(value) for value in (dx, dy)):
        raise PatternError("Edge movement must use finite coordinates.")
    grouped = {}
    for piece_id, edge_id in selection:
        piece = document.piece(piece_id)
        edge = piece.segment(edge_id)
        grouped.setdefault(piece_id, set()).update((edge.start, edge.end))
    for piece_id, vertices in grouped.items():
        piece = document.piece(piece_id)
        angle = math.radians(piece.layout[2])
        c, s = math.cos(angle), math.sin(angle)
        local_dx, local_dy = dx * c + dy * s, -dx * s + dy * c
        for point_id in vertices:
            point = piece.point(point_id)
            move_point(piece, point_id, point.x + local_dx, point.y + local_dy)


def delete_edges(piece: Piece, edge_ids) -> None:
    """Remove edges, retaining the remaining connected runs as open drafts."""
    wanted = set(edge_ids)
    if not wanted.issubset({edge.id for edge in piece.segments}):
        raise PatternError("A selected edge no longer exists.")
    runs = []
    for part in piece.boundary_parts():
        edges = list(part.segments)
        if not any(edge.id in wanted for edge in edges):
            if edges:
                runs.append(edges)
            continue
        if part.closed:
            cut = next(index for index, edge in enumerate(edges) if edge.id in wanted)
            edges = edges[cut + 1:] + edges[:cut + 1]
        run = []
        for edge in edges:
            if edge.id in wanted:
                if run:
                    runs.append(run)
                    run = []
            else:
                run.append(edge)
        if run:
            runs.append(run)
    piece.segments = [edge for run in runs for edge in run]
    piece.contours = [[edge.id for edge in run] for run in runs] if piece.contours or len(runs) > 1 else []
    piece.closed = bool(runs) and all(run[-1].end == run[0].start for run in runs)
    piece.metadata.pop("template", None)


def delete_points(piece: Piece, point_ids) -> None:
    """Remove selected vertices and reconnect their neighbors in boundary order."""
    wanted = set(point_ids)
    if not wanted:
        return
    if not wanted.issubset({point.id for point in piece.points}):
        raise PatternError("A selected point no longer exists.")
    if piece.contours:
        parts = deepcopy(piece.boundary_parts())
        reduced = []
        for part in parts:
            ids = wanted & {point.id for point in part.points}
            if ids:
                delete_points(part, ids)
                if not part.closed and len(part.segments) == 1:
                    reduced.append(part)
        # A removed lobe may reduce to the edge already owned by its surviving
        # neighbor. Keep that neighbor's edge instead of a duplicate open run.
        for part in reduced:
            edge = part.segments[0]
            for other in parts:
                if other is part or other in reduced:
                    continue
                for candidate in other.segments:
                    same = edge.start == candidate.start and edge.end == candidate.end
                    reverse = edge.start == candidate.end and edge.end == candidate.start
                    controls = candidate.controls if same else list(reversed(candidate.controls))
                    if ((same or reverse) and edge.kind == candidate.kind and
                            all(math.dist(a, b) <= 1e-7 for a, b in zip(edge.controls, controls))):
                        part.segments = []
                        break
                if not part.segments:
                    break
        piece.segments = [edge for part in parts for edge in part.segments]
        piece.contours = [[edge.id for edge in part.segments] for part in parts if part.segments]
        piece.points = [point for point in piece.points if point.id not in wanted]
        piece.closed = bool(piece.segments) and all(part.closed for part in parts if part.segments)
        piece.metadata.pop("template", None)
        return
    ordered = [segment.start for segment in piece.segments]
    if not piece.closed and piece.segments:
        ordered.append(piece.segments[-1].end)
    if not ordered:
        piece.points = [point for point in piece.points if point.id not in wanted]
        piece.closed = False
        piece.metadata.pop("template", None)
        return
    keep = [point_id for point_id in ordered if point_id not in wanted]
    if len(keep) < 2:
        piece.points = [point for point in piece.points if point.id not in wanted]
        piece.segments = []
        piece.closed = False
        piece.metadata.pop("template", None)
        return
    if len(keep) == 2:
        # Prefer the original surviving edge, including its cubic handles.
        direct = next((edge for edge in piece.segments if {edge.start, edge.end} == set(keep)), None)
        if direct is not None:
            piece.points = [point for point in piece.points if point.id not in wanted]
            piece.segments = [deepcopy(direct)]
            piece.closed = False
            piece.metadata.pop("template", None)
            return
        piece.closed = False

    points = {point.id: point for point in piece.points}
    old_segments = {segment.start: segment for segment in piece.segments}
    rebuilt = []
    pair_count = len(keep) if piece.closed else len(keep) - 1
    for i in range(pair_count):
        start_id, end_id = keep[i], keep[(i + 1) % len(keep)]
        cursor, traversed = start_id, []
        while cursor != end_id:
            edge = old_segments.get(cursor)
            if edge is None or len(traversed) >= len(ordered):
                raise PatternError("Could not reconnect the selected boundary points.")
            traversed.append(edge)
            cursor = edge.end
        if len(traversed) == 1:
            rebuilt.append(deepcopy(traversed[0]))
            continue
        curves = [segment for segment in traversed if segment.kind == "cubic"]
        if curves:
            first, last = traversed[0], traversed[-1]
            a, b = points[start_id], points[end_id]
            if first.kind == "cubic":
                first_control = [a.x + 2 * (first.controls[0][0] - a.x),
                                 a.y + 2 * (first.controls[0][1] - a.y)]
            elif last.kind == "cubic":
                first_control = [a.x + (last.controls[0][0] - a.x) / 3,
                                 a.y + (last.controls[0][1] - a.y) / 3]
            else:
                first_control = [a.x + (b.x - a.x) / 3, a.y + (b.y - a.y) / 3]
            if last.kind == "cubic":
                last_control = [b.x + 2 * (last.controls[1][0] - b.x),
                                b.y + 2 * (last.controls[1][1] - b.y)]
            elif first.kind == "cubic":
                last_control = [b.x + (first.controls[1][0] - b.x) / 3,
                                b.y + (first.controls[1][1] - b.y) / 3]
            else:
                last_control = [a.x + (b.x - a.x) * 2 / 3, a.y + (b.y - a.y) * 2 / 3]
            rebuilt.append(Segment(new_id(), start_id, end_id, "cubic",
                                   [list(first_control), list(last_control)]))
        else:
            rebuilt.append(Segment(new_id(), start_id, end_id))
    piece.points = [point for point in piece.points if point.id not in wanted]
    piece.segments = rebuilt
    piece.metadata.pop("template", None)


def split_segment(piece: Piece, segment_id: str, t: float = 0.5) -> str:
    """Insert a vertex on an edge; cubic curves use an exact De Casteljau split."""
    if not 0.0 < t < 1.0:
        raise PatternError("A new point must split the edge between its endpoints.")
    index = next((i for i, segment in enumerate(piece.segments) if segment.id == segment_id), -1)
    if index < 0:
        raise PatternError("The selected edge no longer exists.")
    segment = piece.segments[index]
    start, end = piece.point(segment.start), piece.point(segment.end)
    if segment.kind == "cubic":
        p0, p3 = [start.x, start.y], [end.x, end.y]
        p1, p2 = segment.controls
        lerp = lambda a, b: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
        q0, q1, q2 = lerp(p0, p1), lerp(p1, p2), lerp(p2, p3)
        r0, r1 = lerp(q0, q1), lerp(q1, q2)
        middle = lerp(r0, r1)
        left_controls, right_controls = [q0, r0], [r1, q2]
    else:
        middle = [start.x + (end.x - start.x) * t, start.y + (end.y - start.y) * t]
        left_controls = right_controls = None
    point = Point(new_id(), *middle)
    left = Segment(new_id(), segment.start, point.id, segment.kind,
                   left_controls or [])
    right = Segment(new_id(), point.id, segment.end, segment.kind,
                    right_controls or [])
    piece.points.insert(piece.points.index(start) + 1, point)
    piece.segments[index:index + 1] = [left, right]
    for run in piece.contours:
        if segment_id in run:
            location = run.index(segment_id)
            run[location:location + 1] = [left.id, right.id]
    piece.metadata.pop("template", None)
    return point.id


def resize_piece(piece: Piece, width: float, height: float) -> None:
    x0, y0, x1, y1 = bounds(piece)
    if min(width, height, x1 - x0, y1 - y0) <= 1e-8:
        raise PatternError("Width and height must be positive on a non-flat piece.")
    sx, sy = width / (x1 - x0), height / (y1 - y0)
    for point in piece.points:
        point.x, point.y = x0 + (point.x - x0) * sx, y0 + (point.y - y0) * sy
    for segment in piece.segments:
        for control in segment.controls:
            control[0], control[1] = x0 + (control[0] - x0) * sx, y0 + (control[1] - y0) * sy
    piece.metadata.pop("template", None)


def close_outline(piece: Piece) -> None:
    if piece.contours:
        parts = piece.boundary_parts()
        for part in parts:
            close_outline(part)
        piece.segments = [edge for part in parts for edge in part.segments]
        piece.contours = [[edge.id for edge in part.segments] for part in parts]
        piece.closed = True
        return
    if len(piece.points) < 3 or not piece.segments:
        raise PatternError("Draw at least three points before closing an outline.")
    if piece.segments[-1].end != piece.segments[0].start:
        piece.segments.append(Segment(new_id(), piece.segments[-1].end, piece.segments[0].start))
    piece.closed = True
    if signed_area(piece) < 0:
        reverse_boundary(piece)


class DocumentHistory:
    """Snapshots make a complete gesture/proposal one atomic undo step."""

    def __init__(self, document: PatternDocument):
        validate_integrity(document)
        self.document = deepcopy(document)
        self.undo_stack = []
        self.redo_stack = []
        self.saved_hash = document.content_hash()
        self.generation = 0

    @property
    def dirty(self):
        return self.document.content_hash() != self.saved_hash

    def edit(self, label, operation) -> bool:
        candidate = deepcopy(self.document)
        operation(candidate)
        from .relationships import reconcile_relationships
        reconcile_relationships(self.document, candidate)
        validate_integrity(candidate)
        if candidate.content_hash() == self.document.content_hash():
            return False
        previous = self.document
        candidate.revision = previous.revision + 1
        self.undo_stack.append((label, previous))
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()
        self.document = candidate
        self.generation += 1
        return True

    def _restore(self, source, target):
        if not source:
            return False
        label, previous = source.pop()
        target.append((label, self.document))
        candidate = deepcopy(previous)
        candidate.revision = self.document.revision + 1
        self.document = candidate
        self.generation += 1
        return True

    def undo(self):
        return self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        return self._restore(self.redo_stack, self.undo_stack)

    def mark_saved(self):
        self.saved_hash = self.document.content_hash()
