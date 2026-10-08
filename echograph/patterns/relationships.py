"""Persistent mirror pairs and point welds, independent of the editor and Qt."""
import math

from .document import PatternError, Point, Segment, new_id
from .geometry import bounds


def symmetry_for_piece(document, piece_id):
    pairs = [pair for pair in reversed(document.metadata.get("symmetry_pairs", []))
             if piece_id in (pair["left"], pair["right"])]
    return next((pair for pair in pairs if pair["enabled"]), pairs[0] if pairs else None)


def disable_symmetry(document, piece_id):
    for pair in document.metadata.get("symmetry_pairs", []):
        if piece_id in (pair["left"], pair["right"]):
            pair["enabled"] = False


def _is_welded(document, piece_id):
    return any(piece_id in (weld["left_piece"], weld["right_piece"])
               for weld in document.metadata.get("point_welds", []))


def mirror_piece(document, piece_id):
    from .commands import duplicate_piece
    original = document.piece(piece_id)
    if _is_welded(document, piece_id):
        raise PatternError("A panel with merged points cannot create an active symmetry pair.")
    partner = duplicate_piece(original, mirror=True)
    points = {a.id: b.id for a, b in zip(original.points, partner.points)}
    # Duplication preserves edge order and mirroring reverses it. Match by
    # position so coincident edges in different outlines retain distinct IDs.
    edges = {a.id: b.id for a, b in zip(original.segments, reversed(partner.segments))}
    disable_symmetry(document, piece_id)
    document.pieces.insert(document.pieces.index(original) + 1, partner)
    x0, _, x1, _ = bounds(original)
    document.metadata.setdefault("symmetry_pairs", []).append({
        "id": new_id(), "left": original.id, "right": partner.id,
        "axis_sum": x0 + x1, "points": points, "edges": edges, "enabled": True})
    return partner


def reflect_geometry(source, target, pair):
    """Keep partner IDs stable while mirroring geometry and boundary direction."""
    forward = source.id == pair["left"]
    points = dict(pair["points"]) if forward else {b: a for a, b in pair["points"].items()}
    edges = dict(pair["edges"]) if forward else {b: a for a, b in pair["edges"].items()}
    points = {point.id: points.get(point.id) or new_id() for point in source.points}
    edges = {edge.id: edges.get(edge.id) or new_id() for edge in source.segments}
    axis = pair["axis_sum"]
    target.points = [Point(points[p.id], axis - p.x, p.y) for p in source.points]
    target.segments = [Segment(edges[e.id], points[e.end], points[e.start], e.kind,
                               [[axis - x, y] for x, y in reversed(e.controls)])
                       for e in reversed(source.segments)]
    target.closed = source.closed
    target.contours = [[edges[key] for key in reversed(run)] for run in reversed(source.contours)]
    target.metadata.pop("template", None)
    pair["points"] = points if forward else {b: a for a, b in points.items()}
    pair["edges"] = edges if forward else {b: a for a, b in edges.items()}


def set_symmetry_enabled(document, piece_id, enabled):
    pair = symmetry_for_piece(document, piece_id)
    if pair is None:
        raise PatternError("This panel has no mirrored partner.")
    if enabled:
        if any(_is_welded(document, key) for key in (pair["left"], pair["right"])):
            raise PatternError("Merged panel points cannot also belong to an active symmetry pair.")
        disable_symmetry(document, pair["left"])
        disable_symmetry(document, pair["right"])
        partner_id = pair["right"] if piece_id == pair["left"] else pair["left"]
        reflect_geometry(document.piece(piece_id), document.piece(partner_id), pair)
    pair["enabled"] = bool(enabled)


def world_point(piece, point_id):
    point = piece.point(point_id)
    angle = math.radians(piece.layout[2])
    c, s = math.cos(angle), math.sin(angle)
    return (piece.layout[0] + point.x * c - point.y * s,
            piece.layout[1] + point.x * s + point.y * c)


def _move_world(piece, point_id, position):
    from .commands import move_point
    dx, dy = position[0] - piece.layout[0], position[1] - piece.layout[1]
    angle = math.radians(piece.layout[2])
    c, s = math.cos(angle), math.sin(angle)
    move_point(piece, point_id, dx * c + dy * s, -dx * s + dy * c)


def merge_points(document, source_piece, source_point, target_piece, target_point):
    """Weld vertices in sheet space, keeping the two panel boundaries separate."""
    if source_piece == target_piece:
        raise PatternError("Choose a point on another panel to merge.")
    source, target = document.piece(source_piece), document.piece(target_piece)
    source.point(source_point)
    target.point(target_point)
    disable_symmetry(document, source_piece)
    disable_symmetry(document, target_piece)
    _move_world(source, source_point, world_point(target, target_point))
    weld = {"id": new_id(), "left_piece": source_piece, "left_point": source_point,
            "right_piece": target_piece, "right_point": target_point}
    document.metadata.setdefault("point_welds", []).append(weld)


def combine_panels(document, piece_ids, *, fuse_selection=None, fuse_all=False, tolerance=0.01):
    """Make one panel with explicit outlines; optionally share coincident vertices.

    Geometry stays in sheet space. The earliest selected panel supplies its ID,
    name and local frame. The overlap tolerance is in millimeters.
    """
    from copy import deepcopy
    selected = set(piece_ids)
    panels = [piece for piece in document.pieces if piece.id in selected]
    if not panels or selected != {piece.id for piece in panels}:
        raise PatternError("Select existing panels to combine.")
    if len(panels) < 2 and not (fuse_all or fuse_selection):
        raise PatternError("Select at least two panels to combine.")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise PatternError("Fuse tolerance must be positive and finite.")
    result = panels[0]
    parts = [deepcopy(piece) for piece in panels]
    angle = math.radians(result.layout[2])
    c, s = math.cos(angle), math.sin(angle)
    def local(position):
        dx, dy = position[0] - result.layout[0], position[1] - result.layout[1]
        return [dx * c + dy * s, -dx * s + dy * c]
    eligible = set()
    all_points, all_edges, contours = [], [], []
    for part in parts:
        disable_symmetry(document, part.id)
        for point in part.points:
            if fuse_all or point.id in (fuse_selection or {}).get(part.id, ()):
                eligible.add(point.id)
            point.x, point.y = local(world_point(part, point.id))
        source = document.piece(part.id)
        for edge in part.segments:
            edge.controls = [local(_world_position(source, control)) for control in edge.controls]
        all_points.extend(part.points)
        all_edges.extend(part.segments)
        contours.extend(deepcopy(part.contours) if part.contours else [[edge.id for edge in part.segments]])
    remap, buckets = {}, {}
    for point in all_points:
        remap[point.id] = point.id
        if point.id not in eligible:
            continue
        cell = (math.floor(point.x / tolerance), math.floor(point.y / tolerance))
        neighbors = [other for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                     for other in buckets.get((cell[0] + dx, cell[1] + dy), ())]
        match = next((other for other in neighbors if math.dist((point.x, point.y), (other.x, other.y)) <= tolerance), None)
        if match:
            remap[point.id] = match.id
        else:
            buckets.setdefault(cell, []).append(point)
    for edge in all_edges:
        edge.start, edge.end = remap[edge.start], remap[edge.end]
    result.points = [point for point in all_points if remap[point.id] == point.id]
    result.segments = all_edges
    result.contours = [run for run in contours if run]
    result.closed = all(part.closed for part in parts)
    if fuse_all or fuse_selection:
        _dissolve_fused_edges(result, tolerance, {remap[key] for key in eligible})
    result.metadata.pop("template", None)
    document.pieces[:] = [piece for piece in document.pieces if piece.id not in selected or piece is result]
    for weld in document.metadata.get("point_welds", []):
        for side in ("left", "right"):
            if weld[side + "_piece"] in selected:
                weld[side + "_piece"] = result.id
                weld[side + "_point"] = remap[weld[side + "_point"]]
    document.metadata["point_welds"] = [w for w in document.metadata.get("point_welds", [])
                                       if w["left_piece"] != w["right_piece"]]
    return result


def _dissolve_fused_edges(piece, tolerance, eligible):
    """Splice original boundary runs across matching, oppositely directed seams.

    Following the original successor at ordinary vertices keeps outlines that
    touch at just one point separate. Only an actual shared edge joins runs.
    """
    edges = {edge.id: edge for edge in piece.segments}
    vertices = {point.id: (point.x, point.y) for point in piece.points}
    formerly_used = {key for edge in piece.segments for key in (edge.start, edge.end)}
    def collapsed(edge):
        return edge.start in eligible and edge.start == edge.end and all(
            math.dist(control, vertices[edge.start]) <= tolerance for control in edge.controls)

    runs = [[key for key in run if not collapsed(edges[key])] for run in piece.contours]
    runs = [run for run in runs if run]

    def straight(edge):
        if edge.kind == "line":
            return True
        a, b = vertices[edge.start], vertices[edge.end]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length_sq = dx * dx + dy * dy
        if not length_sq:
            return False
        projections = []
        for x, y in edge.controls:
            t = ((x - a[0]) * dx + (y - a[1]) * dy) / length_sq
            if not 0 <= t <= 1 or abs((x - a[0]) * dy - (y - a[1]) * dx) > tolerance * math.sqrt(length_sq):
                return False
            projections.append(t)
        return projections[0] <= projections[1]

    def matches(a, b):
        if straight(a) and straight(b):
            return True
        if a.kind != b.kind:
            return False
        controls = b.controls if a.start == b.start else list(reversed(b.controls))
        return all(math.dist(x, y) <= tolerance for x, y in zip(a.controls, controls))

    # Group geometrically identical edges, including reversed cubic handles.
    buckets, classes, representatives = {}, {}, []
    for run in runs:
        for key in run:
            edge = edges[key]
            endpoints = tuple(sorted((edge.start, edge.end)))
            candidates = buckets.setdefault(endpoints, [])
            group = next((index for index in candidates if matches(edge, representatives[index])), None)
            if group is None:
                group = len(representatives)
                representatives.append(edge)
                candidates.append(group)
            classes[key] = group

    # Identical complete outlines are duplicate exterior boundaries, rather
    # than an interior seam: retain one instead of cancelling both outlines.
    unique, seen = [], set()
    for run in runs:
        sequence = tuple(classes[key] for key in run)
        closed = edges[run[-1]].end == edges[run[0]].start
        if closed:
            smallest = min(sequence)
            signature = (True, min(order[index:] + order[:index]
                                   for order in (sequence, tuple(reversed(sequence)))
                                   for index, value in enumerate(order) if value == smallest))
        else:
            signature = (False, min(sequence, tuple(reversed(sequence))))
        can_dissolve = all(edges[key].start in eligible and edges[key].end in eligible for key in run)
        if not can_dissolve or signature not in seen:
            seen.add(signature)
            unique.append(run)

    members, successor, ordered = {}, {}, []
    for index, run in enumerate(unique):
        closed = edges[run[-1]].end == edges[run[0]].start
        for position, key in enumerate(run):
            members.setdefault(classes[key], []).append((index, key))
            successor[key] = run[position + 1] if position + 1 < len(run) else run[0] if closed else None
            ordered.append(key)
    seams = {}
    for group in members.values():
        if len(group) != 2 or group[0][0] == group[1][0]:
            continue
        a, b = (edges[entry[1]] for entry in group)
        if (a.start != a.end and a.start == b.end and a.end == b.start
                and a.start in eligible and a.end in eligible):
            seams[a.id], seams[b.id] = b.id, a.id

    remaining = [key for key in ordered if key not in seams]
    following = {}
    for key in remaining:
        candidate, visited = successor[key], set()
        while candidate in seams:
            if candidate in visited:
                candidate = None
                break
            visited.add(candidate)
            candidate = successor[seams[candidate]]
        following[key] = candidate
    predecessors = {key for key in following.values() if key is not None}
    starts = [key for key in remaining if key not in predecessors]
    rebuilt, visited = [], set()
    for start in starts + remaining:
        if start in visited:
            continue
        run, key = [], start
        while key is not None and key not in visited:
            visited.add(key)
            run.append(key)
            key = following[key]
        rebuilt.append(run)
    piece.contours = rebuilt
    piece.segments = [edges[key] for run in rebuilt for key in run]
    used = {key for edge in piece.segments for key in (edge.start, edge.end)}
    piece.points = [point for point in piece.points if point.id in used or point.id not in formerly_used]
    piece.closed = bool(rebuilt) and all(edges[run[-1]].end == edges[run[0]].start for run in rebuilt)


def _world_position(piece, position):
    angle = math.radians(piece.layout[2])
    c, s = math.cos(angle), math.sin(angle)
    return (piece.layout[0] + position[0] * c - position[1] * s,
            piece.layout[1] + position[0] * s + position[1] * c)


def fuse_selected_points(document, selection):
    return combine_panels(document, [key for key, ids in selection.items() if ids], fuse_selection=selection)


def _geometry(piece):
    return ([(p.id, p.x, p.y) for p in piece.points],
            [(e.id, e.start, e.end, e.kind, e.controls) for e in piece.segments], piece.closed, piece.contours)


def reconcile_relationships(previous, document):
    """Apply linked edits as part of the same history transaction."""
    pieces = {p.id: p for p in document.pieces}
    old_pieces = {p.id: p for p in previous.pieces}
    pairs = document.metadata.get("symmetry_pairs", [])
    pairs[:] = [pair for pair in pairs if pair["left"] in pieces and pair["right"] in pieces]
    old_pairs = {pair["id"]: pair for pair in previous.metadata.get("symmetry_pairs", [])}
    for pair in pairs:
        old = old_pairs.get(pair["id"])
        if not pair["enabled"] or not old or not old["enabled"]:
            continue
        left, right = pieces[pair["left"]], pieces[pair["right"]]
        changed_left = _geometry(left) != _geometry(old_pieces[left.id])
        changed_right = _geometry(right) != _geometry(old_pieces[right.id])
        if changed_left and changed_right:
            raise PatternError("Edit one side of a symmetry pair at a time, or disable symmetry first.")
        if changed_left:
            reflect_geometry(left, right, pair)
        elif changed_right:
            reflect_geometry(right, left, pair)

    available = {(p.id, point.id) for p in document.pieces for point in p.points}
    welds = document.metadata.get("point_welds", [])
    welds[:] = [w for w in welds if (w["left_piece"], w["left_point"]) in available
               and (w["right_piece"], w["right_point"]) in available]
    # Connected welds behave as one vertex, including rotated/arranged panels.
    groups = []
    for weld in welds:
        group = {(weld["left_piece"], weld["left_point"]), (weld["right_piece"], weld["right_point"])}
        touching = [g for g in groups if g & group]
        for other in touching:
            group |= other
            groups.remove(other)
        groups.append(group)
    for group in groups:
        moved = []
        for piece_id, point_id in sorted(group):
            old = old_pieces.get(piece_id)
            position = world_point(pieces[piece_id], point_id)
            if old and any(p.id == point_id for p in old.points):
                if math.dist(position, world_point(old, point_id)) > 1e-7:
                    moved.append(position)
        if not moved:
            continue
        if any(math.dist(moved[0], p) > 1e-6 for p in moved[1:]):
            raise PatternError("Merged points must move together; edit one panel at a time.")
        for piece_id, point_id in group:
            if math.dist(world_point(pieces[piece_id], point_id), moved[0]) > 1e-7:
                _move_world(pieces[piece_id], point_id, moved[0])

    from .sewing import reconcile_sewing

    reconcile_sewing(previous, document)


def validate_relationships(document):
    from .sewing import validate_sewing

    validate_sewing(document)
    pieces = {p.id: p for p in document.pieces}
    active = set()
    seen = set()
    for key in ("symmetry_pairs", "point_welds"):
        rows = document.metadata.get(key, [])
        if not isinstance(rows, list):
            raise PatternError(f"{key} must be a list.")
        for row in rows:
            expected = ({"id", "left", "right", "axis_sum", "points", "edges", "enabled"}
                        if key == "symmetry_pairs" else
                        {"id", "left_piece", "left_point", "right_piece", "right_point"})
            if not isinstance(row, dict) or set(row) != expected:
                raise PatternError(f"Invalid {key} record.")
            if not isinstance(row["id"], str) or not row["id"] or row["id"] in seen:
                raise PatternError("Relationship IDs must be nonempty and unique.")
            seen.add(row["id"])
            a, b = (row["left"], row["right"]) if key == "symmetry_pairs" else (row["left_piece"], row["right_piece"])
            if not isinstance(a, str) or not isinstance(b, str) or a == b or a not in pieces or b not in pieces:
                raise PatternError("A relationship references a missing or identical panel.")
            if key == "symmetry_pairs":
                if type(row["enabled"]) is not bool or type(row["axis_sum"]) not in (float, int) or not math.isfinite(row["axis_sum"]):
                    raise PatternError("Invalid symmetry axis or enabled state.")
                for entity, attr in (("points", "points"), ("edges", "segments")):
                    mapping = row[entity]
                    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
                        raise PatternError("Invalid symmetry entity mapping.")
                    if row["enabled"] and (set(mapping) != {e.id for e in getattr(pieces[a], attr)}
                                           or set(mapping.values()) != {e.id for e in getattr(pieces[b], attr)}
                                           or len(mapping) != len(set(mapping.values()))):
                        raise PatternError("An active symmetry map must match both panel boundaries.")
                if row["enabled"]:
                    if a in active or b in active:
                        raise PatternError("A panel can have only one active symmetry partner.")
                    active.update((a, b))
            elif (not any(p.id == row["left_point"] for p in pieces[a].points)
                  or not any(p.id == row["right_point"] for p in pieces[b].points)):
                raise PatternError("A point merge references a missing vertex.")
    if any(_is_welded(document, piece_id) for piece_id in active):
        raise PatternError("Merged panels cannot also have active symmetry.")
