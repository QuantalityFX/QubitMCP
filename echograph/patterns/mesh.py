"""Convert saved sewing panels to flat, triangulated scene geometry (meters)."""
from __future__ import annotations

import math
import hashlib
import json
from bisect import bisect_right
from pathlib import Path
import tempfile

from .document import PatternDocument, PatternError
from .geometry import sample_segment, validate_piece

MESH_SPACING_MM = 10.0
MAX_MESH_POINTS = 100_000


def sample_boundary(piece, spacing=MESH_SPACING_MM):
    """Equal arc-length intervals per authored edge, retaining every corner."""
    points = []
    for segment in piece.segments:
        samples = sample_segment(piece, segment, min(0.05, spacing * 0.005))
        lengths = [0.0]
        for a, b in zip(samples, samples[1:]):
            lengths.append(lengths[-1] + math.dist(a, b))
        count = max(1, math.ceil(lengths[-1] / spacing))
        if len(points) + count > MAX_MESH_POINTS:
            raise PatternError("Panel is too large for the 10 mm scene mesh spacing.")
        for i in range(count):
            distance = lengths[-1] * i / count
            index = min(bisect_right(lengths, distance) - 1, len(samples) - 2)
            a, b = samples[index:index + 2]
            t = (distance - lengths[index]) / (lengths[index + 1] - lengths[index])
            points.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return points


def _inside_and_distance(points, boundary):
    """Vectorized winding parity and distance to the sampled closed outline."""
    import numpy as np

    inside = np.zeros(len(points), dtype=bool)
    distances = np.full(len(points), np.inf)
    x, y = points[:, 0], points[:, 1]
    for a, b in zip(boundary, boundary[1:] + boundary[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if dy:
            inside ^= ((a[1] > y) != (b[1] > y)) & (x < a[0] + (y - a[1]) * dx / dy)
        t = np.clip(((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy), 0, 1)
        distances = np.minimum(distances, (x - a[0] - t * dx)**2 + (y - a[1] - t * dy)**2)
    return inside, distances


def _constrain_boundary(points, faces, count):
    """Recover polygon edges in the Delaunay mesh by flipping crossing diagonals."""
    def cross(a, b, c):
        a, b, c = points[a], points[b], points[c]
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def crosses(a, b, c, d):
        return (cross(a, b, c) * cross(a, b, d) < 0
                and cross(c, d, a) * cross(c, d, b) < 0)

    def edges(face):
        return [tuple(sorted((face[i], face[(i + 1) % 3]))) for i in range(3)]

    adjacent = {}
    for index, face in enumerate(faces):
        for edge in edges(face):
            adjacent.setdefault(edge, set()).add(index)
    locked = set()
    for a in range(count):
        b = (a + 1) % count
        target = tuple(sorted((a, b)))
        visited = set()
        while target not in adjacent:
            candidates = []
            for (u, v), owners in adjacent.items():
                if len(owners) != 2 or (u, v) in locked or not crosses(a, b, u, v):
                    continue
                i, j = sorted(owners)
                c = next(p for p in faces[i] if p not in (u, v))
                d = next(p for p in faces[j] if p not in (u, v))
                diagonal = tuple(sorted((c, d)))
                if diagonal in visited or diagonal in adjacent or not crosses(u, v, c, d):
                    continue
                candidates.append((crosses(a, b, c, d), u, v, c, d, i, j))
            if not candidates:
                raise PatternError("Could not preserve a panel boundary in the scene mesh.")
            _, u, v, c, d, i, j = min(candidates)
            visited.add((min(u, v), max(u, v)))
            for index in (i, j):
                for edge in edges(faces[index]):
                    adjacent[edge].remove(index)
                    if not adjacent[edge]:
                        del adjacent[edge]
            for index, face in ((i, (c, d, u)), (j, (d, c, v))):
                if cross(*face) < 0:
                    face = (face[0], face[2], face[1])
                faces[index] = face
                for edge in edges(face):
                    adjacent.setdefault(edge, set()).add(index)
        locked.add(target)
    return faces


def panel_mesh(piece, spacing=MESH_SPACING_MM):
    """Fill a constrained, evenly sampled boundary with a triangular point lattice."""
    import numpy as np
    from scipy.spatial import Delaunay, QhullError

    if not math.isfinite(spacing) or spacing <= 0:
        raise PatternError("Mesh spacing must be positive and finite.")
    boundary = sample_boundary(piece, spacing)
    low = np.min(boundary, axis=0)
    high = np.max(boundary, axis=0)
    row_step = spacing * math.sqrt(3) / 2
    columns = math.ceil((high[0] - low[0]) / spacing)
    rows = math.ceil((high[1] - low[1]) / row_step)
    if columns * rows + len(boundary) > MAX_MESH_POINTS:
        raise PatternError("Panel is too large for the 10 mm scene mesh spacing.")
    lattice = [(low[0] + (col + 0.5 + 0.5 * (row % 2)) * spacing,
                low[1] + (row + 0.5) * row_step)
               for row in range(rows) for col in range(columns)]
    interior = np.asarray(lattice, dtype=float).reshape(-1, 2)
    mask, distances = _inside_and_distance(interior, boundary)
    # Leave room for the regularly sampled border instead of almost coincident
    # interior vertices, which would create tiny sliver triangles.
    interior = interior[mask & (distances >= (spacing * 0.45)**2)]
    points = np.concatenate((np.asarray(boundary), interior))
    try:
        faces = [tuple(map(int, face)) for face in Delaunay(points).simplices]
    except QhullError as exc:
        raise PatternError("Could not triangulate the panel surface.") from exc
    faces = _constrain_boundary(points, faces, len(boundary))
    triangles = points[np.asarray(faces)]
    ab = triangles[:, 1] - triangles[:, 0]
    ac = triangles[:, 2] - triangles[:, 0]
    double_area = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
    centroids = triangles.mean(axis=1)
    inside, _ = _inside_and_distance(centroids, boundary)
    # Qhull can emit effectively collinear triangles along a straight sampled
    # edge. Their centroids lie on the boundary and parity alone is ambiguous.
    usable = inside & (double_area > max(1e-16, float(max(high - low))**2 * 1e-12))
    faces = [face for face, keep in zip(faces, usable) if keep]
    if not faces:
        raise PatternError("The panel has no usable mesh area.")
    return [tuple(p) for p in points], faces


def document_obj(document: PatternDocument) -> str:
    """Preserve sheet placement and panel names; sample Beziers before meshing."""
    if not document.pieces:
        raise PatternError("Add a closed panel and save the pattern first.")
    diagnostics = [d for piece in document.pieces for d in validate_piece(piece)]
    if diagnostics:
        raise PatternError(diagnostics[0].message)
    lines = ["# Qubit pattern panels; millimeters converted to meters"]
    offset = 1
    for piece in document.pieces:
        # OBJ names are single-line labels, never paths or directives.
        label = "_".join(piece.name.split()) or piece.id
        lines.append(f"o {label}")
        angle = math.radians(piece.layout[2])
        co, si = math.cos(angle), math.sin(angle)
        for part in piece.boundary_parts():
            points, faces = panel_mesh(part)
            for x, y in points:
                px = (co * x - si * y + piece.layout[0]) * 0.001
                py = (si * x + co * y + piece.layout[1]) * 0.001
                lines.append(f"v {px:.12g} {py:.12g} 0")
            for face in faces:
                lines.append("f " + " ".join(str(index + offset) for index in face))
            offset += len(points)
    return "\n".join(lines) + "\n"


def cached_mesh(document: PatternDocument, directory: Path | None = None) -> Path:
    """Content-addressed files let the viewport reload changed geometry reliably."""
    directory = directory or Path(tempfile.gettempdir()) / "EchoGraph" / "patterns"
    # Sewing/symmetry annotations and document metadata do not change the flat
    # mesh. Hash only inputs used by document_obj so sewing-only saves reuse it.
    fields = {"id", "name", "points", "segments", "closed", "layout", "contours"}
    geometry = {"units": document.units,
                "pieces": [{key: value for key, value in piece.items() if key in fields}
                           for piece in document.to_dict()["pieces"]]}
    signature = hashlib.sha256(json.dumps(geometry, sort_keys=True, allow_nan=False,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()
    path = directory / f"panels_v3_{signature}.obj"
    if not path.is_file():
        contents = document_obj(document)
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return path
