"""Named panel placement, independent of rendering and solver state."""
import numpy as np
from scipy.spatial.transform import Rotation
import warnings


PIECE_SEPARATOR = "::pattern_piece::"


class PlacementPose:
    """A static Pattern preview needs positions, without building constraints."""
    def __init__(self, asset, settings, positions):
        self.asset, self.settings = asset, settings
        self.x = np.array(positions, copy=True)
        self.rest = self.x.copy()
        self.velocity = np.zeros_like(self.x)


def piece_owner(owner, piece_id):
    return str(owner) + PIECE_SEPARATOR + str(piece_id)


def split_piece_owner(owner):
    parent, separator, piece_id = str(owner or "").rpartition(PIECE_SEPARATOR)
    return (parent, piece_id) if separator and parent and piece_id else None


def transform_points(points, matrix):
    result = np.c_[points, np.ones(len(points))] @ np.asarray(matrix)
    return result[:, :3] / np.where(np.abs(result[:, 3:4]) > 1e-12, result[:, 3:4], 1)


def xform_matrix(xform):
    """Match Scene's row vectors and negated-angle gizmo convention."""
    if "matrix" in xform:
        return np.array(xform["matrix"], dtype=float, copy=True)
    matrix = np.eye(4)
    matrix[:3, :3] = Rotation.from_euler("XYZ", xform.get("rot", [0, 0, 0]), degrees=True).as_matrix()
    matrix[:3, :3] *= np.asarray(xform.get("scl", [1, 1, 1]))[None, :]
    matrix[3, :3] = xform.get("pos", [0, 0, 0])
    return matrix


def matrix_xform(matrix):
    linear = np.array(matrix[:3, :3], copy=True)
    scale = np.maximum(np.linalg.norm(linear, axis=0), 1e-8)
    if np.linalg.det(linear) < 0:
        scale[2] *= -1
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Gimbal lock detected.*", category=UserWarning)
        rotation = Rotation.from_matrix(linear / scale[None, :]).as_euler("XYZ", degrees=True)
    return {"pos": np.asarray(matrix[3, :3]).tolist(), "rot": rotation.tolist(), "scl": scale.tolist()}


def piece_xform(piece, placements, units):
    saved = placements.get(piece["id"])
    if saved is not None:
        result = {**saved, "pos": (np.asarray(saved["pos"]) * units).tolist()}
        if "matrix" in saved:
            matrix = np.array(saved["matrix"], copy=True)
            matrix[3, :3] *= units
            result["matrix"] = matrix.tolist()
        return result
    return {"pos": (piece["center"] * units).tolist(), "rot": [0, 0, 0], "scl": [1, 1, 1]}


def validate_placements(placements):
    if not isinstance(placements, dict):
        raise ValueError("Pattern piece placements must be an object.")
    for xform in placements.values():
        if not isinstance(xform, dict):
            raise ValueError("Each pattern piece needs position, rotation, and scale.")
        for name in ("pos", "rot", "scl"):
            value = np.asarray(xform.get(name), dtype=float)
            if value.shape != (3,) or not np.isfinite(value).all():
                raise ValueError("Pattern piece transforms need three finite values per axis.")
            if name == "scl" and np.any(np.abs(value) < 1e-8):
                raise ValueError("A pattern piece cannot have zero scale.")
        if "matrix" in xform:
            matrix = np.asarray(xform["matrix"], dtype=float)
            if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                    or abs(np.linalg.det(matrix[:3, :3])) < 1e-12):
                raise ValueError("Invalid pattern piece placement matrix.")
    return placements


def placed_positions(asset, placements, units, parent_matrix):
    rest = np.array(asset.positions * units, copy=True)
    for piece in asset.pieces():
        center = piece["center"] * units
        pose = xform_matrix(piece_xform(piece, placements, units)) @ parent_matrix
        rest[piece["vertices"]] = transform_points(rest[piece["vertices"]] - center, pose)
    return rest / units
