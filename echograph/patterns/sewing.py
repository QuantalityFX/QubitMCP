"""Persistent edge pairs for sewing annotations; independent of Qt."""
import math

from .document import PatternError, new_id


def add_sew_pair(document, left_piece, left_edge, right_piece, right_edge):
    available = {(p.id, edge.id) for p in document.pieces for edge in p.segments}
    left, right = (left_piece, left_edge), (right_piece, right_edge)
    if left == right:
        raise PatternError("Choose two different edges to sew.")
    if left not in available or right not in available:
        raise PatternError("A sewing edge no longer exists.")
    pairs = document.metadata.setdefault("sew_pairs", [])
    for pair in pairs:
        used = {(pair["left_piece"], pair["left_edge"]), (pair["right_piece"], pair["right_edge"])}
        if left in used or right in used:
            raise PatternError("This edge is already sewn. Undo the seam before pairing it again.")
    from .relationships import world_point

    a, b = document.piece(left_piece), document.piece(right_piece)
    ae, be = a.segment(left_edge), b.segment(right_edge)
    a0, a1 = world_point(a, ae.start), world_point(a, ae.end)
    b0, b1 = world_point(b, be.start), world_point(b, be.end)
    reverse = math.dist(a0, b1) + math.dist(a1, b0) < math.dist(a0, b0) + math.dist(a1, b1)
    pair = {"id": new_id(), "number": max((p["number"] for p in pairs), default=0) + 1,
            "left_piece": left_piece, "left_edge": left_edge,
            "right_piece": right_piece, "right_edge": right_edge, "reverse": reverse}
    pairs.append(pair)
    return pair


def reconcile_sewing(previous, document):
    """Retain pairs through placement/curve edits; prune deleted or split edges."""
    available = {edge.id: (p.id, edge) for p in document.pieces for edge in p.segments}
    old_edges = {edge.id: edge for p in previous.pieces for edge in p.segments}
    retained = []
    for pair in document.metadata.get("sew_pairs", []):
        valid = True
        for side in ("left", "right"):
            edge_id = pair[side + "_edge"]
            if edge_id not in available:
                valid = False
                break
            owner, edge = available[edge_id]
            old = old_edges.get(edge_id)
            if old and {old.start, old.end} != {edge.start, edge.end}:
                valid = False
                break
            if old and old.start == edge.end and old.end == edge.start:
                pair["reverse"] = not pair["reverse"]
            pair[side + "_piece"] = owner
        if valid:
            retained.append(pair)
    if "sew_pairs" in document.metadata:
        document.metadata["sew_pairs"] = retained


def validate_sewing(document):
    pairs = document.metadata.get("sew_pairs", [])
    if not isinstance(pairs, list):
        raise PatternError("Sewing pairs must be a list.")
    available = {(p.id, edge.id) for p in document.pieces for edge in p.segments}
    ids, numbers, used = set(), set(), set()
    fields = {"id", "number", "left_piece", "left_edge", "right_piece", "right_edge", "reverse"}
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != fields:
            raise PatternError("Invalid sewing pair fields.")
        if not isinstance(pair["id"], str) or not pair["id"] or pair["id"] in ids:
            raise PatternError("Sewing pair IDs must be nonempty and unique.")
        if type(pair["number"]) is not int or pair["number"] <= 0 or pair["number"] in numbers:
            raise PatternError("Sewing pair numbers must be positive and unique.")
        if type(pair["reverse"]) is not bool:
            raise PatternError("Invalid sewing direction.")
        ids.add(pair["id"])
        numbers.add(pair["number"])
        for side in ("left", "right"):
            owner, edge = pair[side + "_piece"], pair[side + "_edge"]
            if not isinstance(owner, str) or not isinstance(edge, str) or (owner, edge) not in available:
                raise PatternError("A sewing pair references a missing edge.")
            if (owner, edge) in used:
                raise PatternError("Each edge can belong to only one sewing pair.")
            used.add((owner, edge))
