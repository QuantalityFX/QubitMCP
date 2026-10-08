"""Qt-free pattern records. Coordinates are millimeters in local X-right/Y-up space."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import math
from uuid import uuid4

SCHEMA = "qubit.pattern_document.v1"
MAX_PIECES = 200
MAX_POINTS = 2000


class PatternError(ValueError):
    pass


def new_id() -> str:
    return uuid4().hex


@dataclass
class Point:
    id: str
    x: float
    y: float


@dataclass
class Segment:
    id: str
    start: str
    end: str
    kind: str = "line"
    controls: list[list[float]] = field(default_factory=list)


@dataclass
class Piece:
    id: str
    name: str
    boundary_id: str
    points: list[Point] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    closed: bool = True
    # Sheet arrangement is independent of rest geometry.
    layout: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    metadata: dict = field(default_factory=dict)

    # Optional explicit boundary runs allow one panel to contain several outlines.
    contours: list[list[str]] = field(default_factory=list)

    def point(self, point_id: str) -> Point:
        return next(p for p in self.points if p.id == point_id)

    def segment(self, segment_id: str) -> Segment:
        return next(s for s in self.segments if s.id == segment_id)

    def boundary_parts(self):
        if not self.contours:
            return [self]
        edges = {edge.id: edge for edge in self.segments}
        parts = []
        for contour in self.contours:
            segments = [edges[key] for key in contour]
            vertices = {key for edge in segments for key in (edge.start, edge.end)}
            parts.append(replace(self, segments=segments, contours=[],
                                 points=[point for point in self.points if point.id in vertices],
                                 closed=bool(segments) and segments[-1].end == segments[0].start))
        return parts


@dataclass
class PatternDocument:
    schema: str = SCHEMA
    document_id: str = field(default_factory=new_id)
    name: str = "Untitled pattern"
    revision: int = 0
    units: str = "mm"
    pieces: list[Piece] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def piece(self, piece_id: str) -> Piece:
        return next(p for p in self.pieces if p.id == piece_id)

    def to_dict(self) -> dict:
        data = asdict(self)
        for piece in data["pieces"]:
            if not piece["contours"]:
                piece.pop("contours")
        return data

    def content_hash(self) -> str:
        data = self.to_dict()
        data.pop("revision")
        return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict) -> PatternDocument:
        _keys(data, {"schema", "document_id", "name", "revision", "units", "pieces", "metadata"}, "document")
        if data["schema"] != SCHEMA:
            raise PatternError(f"Unsupported pattern schema: {data['schema']!r}")
        if not isinstance(data["pieces"], list) or len(data["pieces"]) > MAX_PIECES:
            raise PatternError(f"A pattern must contain a list of at most {MAX_PIECES} pieces.")
        pieces = []
        for entry in data["pieces"]:
            _keys(entry, {"id", "name", "boundary_id", "points", "segments", "closed", "layout", "metadata"}
                  | ({"contours"} if "contours" in entry else set()), "piece")
            for key in ("points", "segments"):
                if not isinstance(entry[key], list) or len(entry[key]) > MAX_POINTS:
                    raise PatternError(f"A piece supports at most {MAX_POINTS} {key}.")
            points, segments = [], []
            for point in entry["points"]:
                _keys(point, {"id", "x", "y"}, "point")
                points.append(Point(**point))
            for segment in entry["segments"]:
                _keys(segment, {"id", "start", "end", "kind", "controls"}, "segment")
                segments.append(Segment(**segment))
            pieces.append(Piece(**{**entry, "points": points, "segments": segments}))
        doc = cls(**{**data, "pieces": pieces})
        validate_integrity(doc)
        return doc


def _keys(value, keys: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise PatternError(f"Invalid {label} fields; expected {', '.join(sorted(keys))}.")


def _finite(value) -> bool:
    return type(value) in (int, float) and abs(value) <= 1e9 and math.isfinite(value)


def validate_integrity(doc: PatternDocument) -> None:
    """Validate serialization integrity; an unfinished or intersecting outline is still saveable."""
    if doc.schema != SCHEMA or doc.units != "mm":
        raise PatternError("Unsupported pattern schema or units (native units must be mm).")
    if type(doc.revision) is not int or doc.revision < 0:
        raise PatternError("Revision must be a nonnegative integer.")
    if not isinstance(doc.name, str) or not isinstance(doc.metadata, dict):
        raise PatternError("Invalid document name or metadata.")
    if not isinstance(doc.pieces, list) or len(doc.pieces) > MAX_PIECES:
        raise PatternError(f"A pattern supports at most {MAX_PIECES} pieces.")
    seen = set()

    def identity(value):
        if not isinstance(value, str) or not value or value in seen:
            raise PatternError("Pattern entity IDs must be nonempty and unique.")
        seen.add(value)

    identity(doc.document_id)
    for piece in doc.pieces:
        identity(piece.id)
        identity(piece.boundary_id)
        if not isinstance(piece.name, str) or not isinstance(piece.metadata, dict) or type(piece.closed) is not bool:
            raise PatternError("Invalid piece name, metadata, or closed flag.")
        if not isinstance(piece.layout, list) or len(piece.layout) != 3 or not all(map(_finite, piece.layout)):
            raise PatternError("Piece layout must be three finite numbers (x, y, rotation).")
        if len(piece.points) > MAX_POINTS or len(piece.segments) > MAX_POINTS:
            raise PatternError(f"A piece supports at most {MAX_POINTS} points/segments.")
        point_ids = set()
        for point in piece.points:
            identity(point.id)
            point_ids.add(point.id)
            if not _finite(point.x) or not _finite(point.y):
                raise PatternError("Point coordinates must be finite numbers within +/- 1e9 mm.")
        for segment in piece.segments:
            identity(segment.id)
            if segment.start not in point_ids or segment.end not in point_ids:
                raise PatternError("An edge references a missing point.")
            if segment.kind not in ("line", "cubic"):
                raise PatternError(f"Unsupported edge type: {segment.kind!r}")
            controls = segment.controls
            if not isinstance(controls, list) or len(controls) != (2 if segment.kind == "cubic" else 0):
                raise PatternError("A cubic needs two control handles; a line needs none.")
            if any(not isinstance(c, list) or len(c) != 2 or not all(map(_finite, c)) for c in controls):
                raise PatternError("Curve handles must contain two finite coordinates.")
        if not isinstance(piece.contours, list) or len(piece.contours) > MAX_POINTS:
            raise PatternError("Panel contours must be a list of boundary edge lists.")
        if piece.contours:
            if any(not isinstance(run, list) or not run or any(not isinstance(key, str) for key in run)
                   for run in piece.contours):
                raise PatternError("Each contour must contain boundary edge IDs.")
            listed = [key for run in piece.contours for key in run]
            if len(listed) != len(set(listed)) or set(listed) != {edge.id for edge in piece.segments}:
                raise PatternError("Contours must reference every boundary edge exactly once.")
        for part in piece.boundary_parts():
            for a, b in zip(part.segments, part.segments[1:]):
                if a.end != b.start:
                    raise PatternError("Boundary edges must form one ordered chain.")
    from .relationships import validate_relationships
    validate_relationships(doc)
    try:
        json.dumps(doc.to_dict(), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PatternError(f"Metadata must contain finite JSON data: {exc}") from exc
