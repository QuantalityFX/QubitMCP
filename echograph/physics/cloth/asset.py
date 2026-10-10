"""Build immutable cloth topology and interpolated sewing constraints."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import tempfile

import numpy as np

from echograph.patterns.document import PatternDocument, PatternError
from echograph.patterns.geometry import sample_segment, validate_piece
from echograph.patterns.mesh import panel_mesh
from echograph.patterns.sewing import validate_sewing


@dataclass
class ClothAsset:
    positions: np.ndarray
    triangles: np.ndarray
    edges: np.ndarray
    bends: np.ndarray
    bend_weights: np.ndarray
    seam_indices: np.ndarray
    seam_weights: np.ndarray
    seam_numbers: np.ndarray
    vertex_pieces: tuple
    signature: str
    piece_names: tuple = ()

    def pieces(self):
        """Stable panel IDs survive names, layout changes, and sewing."""
        owners = np.asarray(self.vertex_pieces)
        names = dict(self.piece_names)
        result = []
        for piece_id in dict.fromkeys(self.vertex_pieces):
            vertices = np.flatnonzero(owners == piece_id)
            faces = np.flatnonzero(owners[self.triangles[:, 0]] == piece_id)
            center = (self.positions[vertices].min(axis=0) + self.positions[vertices].max(axis=0)) * .5
            result.append({"id": piece_id, "name": names.get(piece_id, piece_id),
                           "vertices": vertices, "faces": faces, "center": center})
        return result

    def rest_obj(self):
        directory = Path(tempfile.gettempdir()) / "EchoGraph" / "cloth"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("cloth_v2_" + self.signature + ".obj")
        if not path.exists():
            lines = ["# Cloth rest mesh, meters; named pattern pieces"]
            lines.extend("v %.9g %.9g %.9g" % tuple(p) for p in self.positions)
            for piece in self.pieces():
                label = "_".join(piece["name"].split()) or piece["id"]
                lines.append("o " + label)
                lines.append("g " + piece["id"])
                lines.extend("f %d %d %d" % tuple(t + 1) for t in self.triangles[piece["faces"]])
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def _edge_samples(piece, spacing):
    counts = []
    for segment in piece.segments:
        samples = sample_segment(piece, segment, min(0.05, spacing * 0.005))
        length = sum(math.dist(a, b) for a, b in zip(samples, samples[1:]))
        counts.append(max(1, math.ceil(length / spacing)))
    cursor, result = 0, {}
    total = sum(counts)
    for segment, count in zip(piece.segments, counts):
        result[segment.id] = np.array(list(range(cursor, cursor + count)) + [(cursor + count) % total])
        cursor += count
    return result


def _interpolate(indices, positions, samples):
    distances = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(positions[indices], axis=0), axis=1))]
    distances /= max(distances[-1], 1e-12)
    interval = np.clip(np.searchsorted(distances, samples, side="right") - 1, 0, len(indices) - 2)
    fraction = (samples - distances[interval]) / np.maximum(distances[interval + 1] - distances[interval], 1e-12)
    return np.c_[indices[interval], indices[interval + 1]], np.c_[1 - fraction, fraction]


def build_cloth_asset(document, spacing_mm=15.0):
    """Cache topology by geometry AND sewing; editor-only metadata is excluded."""
    spacing_mm = float(spacing_mm)
    if not math.isfinite(spacing_mm) or not 2 <= spacing_mm <= 100:
        raise PatternError("Cloth mesh spacing must be between 2 and 100 mm.")
    document = document if isinstance(document, PatternDocument) else PatternDocument.from_dict(document)
    relevant = document.to_dict()
    relevant.update(document_id="cloth", name="Cloth", revision=0,
                    metadata={"sew_pairs": document.metadata.get("sew_pairs", [])})
    for piece in relevant["pieces"]:
        piece["metadata"] = {}
    return _build_cached(json.dumps(relevant, sort_keys=True), float(spacing_mm))


@lru_cache(maxsize=8)
def _build_cached(serialized, spacing):
    data = json.loads(serialized)
    document = PatternDocument.from_dict(data)
    validate_sewing(document)
    diagnostics = [d for p in document.pieces for d in validate_piece(p)]
    if diagnostics or not document.pieces:
        raise PatternError(diagnostics[0].message if diagnostics else "Save a closed pattern first.")
    vertices, triangles, owners, edge_lookup = [], [], [], {}
    for piece in document.pieces:
        for part in piece.boundary_parts():
            points, faces = panel_mesh(part, spacing)
            offset = len(vertices)
            positions = np.c_[np.asarray(points, dtype=float) * 0.001, np.zeros(len(points))]
            angle = math.radians(float(piece.layout[2]))
            c, s = math.cos(angle), math.sin(angle)
            positions[:, :2] = positions[:, :2] @ np.array([[c, s], [-s, c]])
            positions[:, :2] += np.asarray(piece.layout[:2]) * 0.001
            vertices.extend(positions)
            triangles.extend(np.asarray(faces) + offset)
            owners.extend([piece.id] * len(points))
            for edge, indices in _edge_samples(part, spacing).items():
                edge_lookup[(piece.id, edge)] = indices + offset
    positions = np.asarray(vertices, dtype=float)
    faces = np.asarray(triangles, dtype=np.int32).reshape(-1, 3)
    adjacent = {}
    for a, b, c in faces:
        for i, j, opposite in ((a, b, c), (b, c, a), (c, a, b)):
            adjacent.setdefault(tuple(sorted((int(i), int(j)))), []).append(int(opposite))
    bends, coefficients = [], []
    for (i, j), opposites in adjacent.items():
        if len(opposites) != 2:
            continue
        indices = [i, j, *opposites]
        # Nullspace of planar affine coordinates: a discrete curvature stencil.
        # Its rest value is zero and remains zero under every rigid transform.
        xy = positions[indices, :2]
        matrix = np.vstack([xy.T, np.ones(4)])
        _, _, vt = np.linalg.svd(matrix, full_matrices=True)
        weights = vt[-1] / max(np.linalg.norm(vt[-1]), 1e-12)
        bends.append(indices)
        coefficients.append(weights)
    seam_indices, seam_weights, seam_numbers = [], [], []
    for pair in document.metadata.get("sew_pairs", []):
        left = edge_lookup[(pair["left_piece"], pair["left_edge"])]
        right = edge_lookup[(pair["right_piece"], pair["right_edge"])]
        if pair.get("reverse"):
            right = right[::-1]
        samples = np.linspace(0, 1, max(len(left), len(right)))
        li, lw = _interpolate(left, positions, samples)
        ri, rw = _interpolate(right, positions, samples)
        seam_indices.extend(np.c_[li, ri])
        seam_weights.extend(np.c_[lw, -rw])
        seam_numbers.extend([pair["number"]] * len(samples))
    signature = hashlib.sha256((serialized + str(spacing)).encode()).hexdigest()[:24]
    arrays = [positions, faces, np.asarray(list(adjacent), dtype=np.int32).reshape(-1, 2),
              np.asarray(bends, dtype=np.int32).reshape(-1, 4), np.asarray(coefficients).reshape(-1, 4),
              np.asarray(seam_indices, dtype=np.int32).reshape(-1, 4),
              np.asarray(seam_weights).reshape(-1, 4), np.asarray(seam_numbers, dtype=np.int32)]
    for array in arrays:
        array.setflags(write=False)
    return ClothAsset(*arrays, tuple(owners), signature, tuple((p.id, p.name) for p in document.pieces))
