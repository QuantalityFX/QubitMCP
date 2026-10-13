"""Persistent elastic boundary edges, independent of the editor and solver."""
import math

from .document import PatternError

DEFAULT_STRENGTH = 1000.0
DEFAULT_REST_RATIO = 0.8


def elastic_for_edge(document, piece_id, edge_id):
    return next((row for row in document.metadata.get("elastic_edges", [])
                 if (row["piece"], row["edge"]) == (piece_id, edge_id)), None)


def set_elastic_edges(document, selection, enabled=True, strength=DEFAULT_STRENGTH,
                      rest_ratio=DEFAULT_REST_RATIO):
    selection = set(selection)
    available = {(piece.id, edge.id) for piece in document.pieces for edge in piece.segments}
    if not selection.issubset(available):
        raise PatternError("An elastic edge no longer exists.")
    if not (math.isfinite(strength) and 0 < strength <= 1000000
            and math.isfinite(rest_ratio) and .01 <= rest_ratio <= 1):
        raise PatternError("Elastic strength must be positive; rest length must be 1–100%.")
    rows = [row for row in document.metadata.get("elastic_edges", [])
            if (row["piece"], row["edge"]) not in selection]
    if enabled:
        rows.extend({"piece": piece, "edge": edge, "strength": float(strength),
                     "rest_ratio": float(rest_ratio)} for piece, edge in sorted(selection))
    document.metadata["elastic_edges"] = rows


def reconcile_elastic(previous, document):
    """Retain annotations through layout, combining, reversal and edge splits."""
    if "elastic_edges" not in document.metadata:
        return
    available = {edge.id: (piece.id, edge) for piece in document.pieces for edge in piece.segments}
    old_edges = {edge.id: edge for piece in previous.pieces for edge in piece.segments}
    old_ids = set(old_edges)
    retained = []
    for row in document.metadata["elastic_edges"]:
        if row["edge"] in available:
            retained.append({**row, "piece": available[row["edge"]][0]})
            continue
        old = old_edges.get(row["edge"])
        if old is None:
            continue
        # A split replaces one edge with two new edges meeting at a new point.
        for piece in document.pieces:
            for left in piece.segments:
                if left.id in old_ids or left.start != old.start:
                    continue
                right = next((edge for edge in piece.segments if edge.id not in old_ids
                              and edge.start == left.end and edge.end == old.end), None)
                if right is not None:
                    retained.extend({**row, "piece": piece.id, "edge": edge.id}
                                    for edge in (left, right))
    document.metadata["elastic_edges"] = retained


def validate_elastic(document):
    rows = document.metadata.get("elastic_edges", [])
    if not isinstance(rows, list):
        raise PatternError("Elastic edges must be a list.")
    available = {(piece.id, edge.id) for piece in document.pieces for edge in piece.segments}
    used = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"piece", "edge", "strength", "rest_ratio"}:
            raise PatternError("Invalid elastic edge fields.")
        key = (row["piece"], row["edge"])
        if not all(isinstance(value, str) for value in key) or key not in available or key in used:
            raise PatternError("An elastic edge is missing or duplicated.")
        used.add(key)
        strength, ratio = row["strength"], row["rest_ratio"]
        if (type(strength) not in (int, float) or not math.isfinite(strength) or not 0 < strength <= 1000000
                or type(ratio) not in (int, float) or not math.isfinite(ratio) or not .01 <= ratio <= 1):
            raise PatternError("Invalid elastic strength or rest length.")
