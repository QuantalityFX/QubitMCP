from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from .fbx_canonical import (
    SkeletalMeshAsset,
    SkeletonAsset,
    VertexInfluence,
    VertexSkin,
)
from .skinned_splat_proxy import transfer_barycentric_skin_weights


_SCHEMA = "qubit.skinned_volume_mesh.v1"
MIN_VOLUME_RESOLUTION = 8
MAX_VOLUME_RESOLUTION = 1024
MIN_REMESH_QUALITY = 1
MAX_REMESH_QUALITY = 100
_MAX_VOLUME_CELLS = 1_250_000
_MAX_OUTPUT_TRIANGLES = 300_000


class SkinnedVolumeMeshError(RuntimeError):
    """Raised when a skinned volume mesh cannot be generated safely."""


@dataclass(frozen=True)
class SkinnedVolumeMeshSettings:
    volume_resolution: int = 32
    remesh_quality: int = 2
    max_influences: int = 4
    fill_interior: bool = True


@dataclass(frozen=True)
class SkinnedVolumeMeshArrays:
    bind_positions: np.ndarray
    triangle_indices: np.ndarray
    joint_indices: np.ndarray
    joint_weights: np.ndarray
    source_triangle_indices: np.ndarray
    source_barycentric: np.ndarray
    volume_occupancy: np.ndarray
    volume_origin: np.ndarray
    voxel_size: float
    source_mesh_names: Tuple[str, ...]
    joint_names: Tuple[str, ...]


@dataclass(frozen=True)
class SkinnedVolumeMeshResult:
    manifest_path: Path
    mesh_obj_path: Path
    volume_path: Path
    skin_npz_path: Path
    mesh_asset: SkeletalMeshAsset
    arrays: SkinnedVolumeMeshArrays
    manifest: Dict[str, Any]


def build_skinned_volume_mesh_arrays(
    skeleton: SkeletonAsset,
    meshes: Sequence[SkeletalMeshAsset],
    settings: SkinnedVolumeMeshSettings | None = None,
) -> SkinnedVolumeMeshArrays:
    settings = settings or SkinnedVolumeMeshSettings()
    _validate_settings(settings)
    skeleton.validate()
    if len(skeleton.joints) > 65535:
        raise SkinnedVolumeMeshError("Skinned volume meshes support at most 65535 joints.")

    prepared = _prepare_source_meshes(skeleton, meshes)
    vertices = prepared["positions"]
    faces = prepared["triangles"]
    occupancy, origin, voxel_size = _voxelize(
        vertices,
        faces,
        int(settings.volume_resolution),
        fill_interior=bool(settings.fill_interior),
    )
    out_vertices, out_faces = _mesh_occupancy(occupancy, origin, voxel_size)
    if int(out_faces.shape[0]) > _MAX_OUTPUT_TRIANGLES:
        raise SkinnedVolumeMeshError(
            f"Volume produced {int(out_faces.shape[0]):,} triangles; lower Volume Resolution."
        )
    out_vertices = _taubin_smooth(
        out_vertices,
        out_faces,
        passes=int(settings.remesh_quality),
    )
    joint_indices, joint_weights, source_triangles, barycentric = _transfer_skin_weights(
        out_vertices,
        prepared["positions"],
        prepared["triangles"],
        prepared["vertex_influences"],
        max_influences=int(settings.max_influences),
    )

    return SkinnedVolumeMeshArrays(
        bind_positions=out_vertices.astype("f4", copy=False),
        triangle_indices=out_faces.astype(np.int32, copy=False),
        joint_indices=joint_indices,
        joint_weights=joint_weights,
        source_triangle_indices=source_triangles,
        source_barycentric=barycentric,
        volume_occupancy=occupancy.astype(bool, copy=False),
        volume_origin=origin.astype("f4", copy=False),
        voxel_size=float(voxel_size),
        source_mesh_names=tuple(prepared["mesh_names"]),
        joint_names=tuple(str(joint.name) for joint in skeleton.joints),
    )


def build_skinned_volume_mesh(
    skeleton: SkeletonAsset,
    meshes: Sequence[SkeletalMeshAsset],
    output_dir: str | Path,
    *,
    asset_name: str = "SkinnedVolumeMesh",
    source_fbx: str = "",
    settings: SkinnedVolumeMeshSettings | None = None,
) -> SkinnedVolumeMeshResult:
    settings = settings or SkinnedVolumeMeshSettings()
    arrays = build_skinned_volume_mesh_arrays(skeleton, meshes, settings=settings)
    mesh_asset = mesh_asset_from_arrays(skeleton, arrays, name=asset_name)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(asset_name)
    mesh_obj_path = out_dir / f"{stem}.obj"
    volume_path = out_dir / f"{stem}.qvolume.npz"
    skin_npz_path = out_dir / f"{stem}.skin.npz"
    manifest_path = out_dir / f"{stem}.qskinned_volume.json"

    write_obj(mesh_obj_path, arrays.bind_positions, arrays.triangle_indices)
    write_volume_npz(volume_path, arrays)
    write_skin_npz(skin_npz_path, arrays, mesh_asset)

    occupied_count = int(np.count_nonzero(arrays.volume_occupancy))
    manifest = {
        "schema": _SCHEMA,
        "mesh_obj": mesh_obj_path.name,
        "volume": volume_path.name,
        "skin_npz": skin_npz_path.name,
        "volume_format": "qubit_sparse_voxel_grid",
        "source_fbx": str(source_fbx or ""),
        "skeleton_name": str(skeleton.name),
        "joint_names": list(arrays.joint_names),
        "source_mesh_names": list(arrays.source_mesh_names),
        "vertex_count": int(arrays.bind_positions.shape[0]),
        "triangle_count": int(arrays.triangle_indices.shape[0]),
        "occupied_voxel_count": occupied_count,
        "volume_shape": [int(value) for value in arrays.volume_occupancy.shape],
        "voxel_size": float(arrays.voxel_size),
        "generator": {
            "method": "filled_voxel_surface_remesh",
            "settings": asdict(settings),
            "safety_limits": {
                "max_resolution": MAX_VOLUME_RESOLUTION,
                "max_volume_cells": _MAX_VOLUME_CELLS,
                "max_output_triangles": _MAX_OUTPUT_TRIANGLES,
            },
        },
    }
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return SkinnedVolumeMeshResult(
        manifest_path=manifest_path,
        mesh_obj_path=mesh_obj_path,
        volume_path=volume_path,
        skin_npz_path=skin_npz_path,
        mesh_asset=mesh_asset,
        arrays=arrays,
        manifest=manifest,
    )


def build_skinned_volume_mesh_from_rig_context(
    rig_context: Dict[str, Any],
    output_dir: str | Path,
    *,
    asset_name: str = "SkinnedVolumeMesh",
    source_fbx: str = "",
    settings: SkinnedVolumeMeshSettings | None = None,
) -> SkinnedVolumeMeshResult:
    skeleton = rig_context.get("skeleton") if isinstance(rig_context, dict) else None
    meshes = rig_context.get("meshes") if isinstance(rig_context, dict) else None
    if not isinstance(skeleton, SkeletonAsset):
        raise SkinnedVolumeMeshError("rig_context does not contain a SkeletonAsset.")
    if not isinstance(meshes, Sequence) or isinstance(meshes, (str, bytes)):
        raise SkinnedVolumeMeshError("rig_context does not contain skeletal meshes.")
    return build_skinned_volume_mesh(
        skeleton,
        meshes,
        output_dir,
        asset_name=asset_name,
        source_fbx=source_fbx,
        settings=settings,
    )


def load_skinned_volume_mesh_asset(
    skin_npz_path: str | Path,
    skeleton: SkeletonAsset,
) -> SkeletalMeshAsset:
    path = Path(skin_npz_path)
    try:
        with np.load(path, allow_pickle=False) as payload:
            schema = str(payload["schema"].item())
            if schema != _SCHEMA:
                raise SkinnedVolumeMeshError(f"Unsupported skin artifact schema: {schema!r}.")
            positions = np.asarray(payload["bind_positions"], dtype="f4").reshape(-1, 3)
            faces = np.asarray(payload["triangle_indices"], dtype=np.int32).reshape(-1, 3)
            joint_indices = np.asarray(payload["joint_indices"], dtype=np.int32)
            joint_weights = np.asarray(payload["joint_weights"], dtype="f4")
            raw_name = str(payload["mesh_name"].item())
    except SkinnedVolumeMeshError:
        raise
    except Exception as exc:
        raise SkinnedVolumeMeshError(f"Could not read generated skin artifact: {exc}") from exc

    if joint_indices.shape != joint_weights.shape or joint_indices.shape[0] != positions.shape[0]:
        raise SkinnedVolumeMeshError("Generated skin artifact arrays are inconsistent.")
    return _mesh_asset_from_skin_arrays(
        skeleton,
        positions,
        faces,
        joint_indices,
        joint_weights,
        name=raw_name or path.stem,
    )


def mesh_asset_from_arrays(
    skeleton: SkeletonAsset,
    arrays: SkinnedVolumeMeshArrays,
    *,
    name: str,
) -> SkeletalMeshAsset:
    return _mesh_asset_from_skin_arrays(
        skeleton,
        arrays.bind_positions,
        arrays.triangle_indices,
        arrays.joint_indices,
        arrays.joint_weights,
        name=name,
    )


def write_obj(path: str | Path, vertices: np.ndarray, faces: np.ndarray) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    positions = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    triangles = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("# QubitMCP skinned volume mesh\n")
        for x, y, z in positions:
            handle.write(f"v {float(x):.9g} {float(y):.9g} {float(z):.9g}\n")
        for a, b, c in triangles:
            handle.write(f"f {int(a) + 1} {int(b) + 1} {int(c) + 1}\n")
    return out


def write_volume_npz(path: str | Path, arrays: SkinnedVolumeMeshArrays) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out,
        schema=np.asarray(_SCHEMA),
        occupancy=arrays.volume_occupancy.astype(np.uint8, copy=False),
        origin=arrays.volume_origin.astype("f4", copy=False),
        voxel_size=np.asarray(float(arrays.voxel_size), dtype="f4"),
    )
    return out


def write_skin_npz(
    path: str | Path,
    arrays: SkinnedVolumeMeshArrays,
    mesh_asset: SkeletalMeshAsset,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out,
        schema=np.asarray(_SCHEMA),
        mesh_name=np.asarray(str(mesh_asset.name)),
        skeleton_name=np.asarray(str(mesh_asset.skeleton_name)),
        bind_positions=arrays.bind_positions.astype("f4", copy=False),
        triangle_indices=arrays.triangle_indices.astype(np.int32, copy=False),
        joint_indices=arrays.joint_indices.astype(np.uint16, copy=False),
        joint_weights=arrays.joint_weights.astype("f4", copy=False),
        source_triangle_indices=arrays.source_triangle_indices.astype(np.int32, copy=False),
        source_barycentric=arrays.source_barycentric.astype("f4", copy=False),
    )
    return out


def _validate_settings(settings: SkinnedVolumeMeshSettings) -> None:
    resolution = int(settings.volume_resolution)
    if resolution < MIN_VOLUME_RESOLUTION or resolution > MAX_VOLUME_RESOLUTION:
        raise SkinnedVolumeMeshError(
            "volume_resolution must be between "
            f"{MIN_VOLUME_RESOLUTION} and {MAX_VOLUME_RESOLUTION}."
        )
    quality = int(settings.remesh_quality)
    if quality < MIN_REMESH_QUALITY or quality > MAX_REMESH_QUALITY:
        raise SkinnedVolumeMeshError(
            "remesh_quality must be between "
            f"{MIN_REMESH_QUALITY} and {MAX_REMESH_QUALITY}."
        )
    influences = int(settings.max_influences)
    if influences < 1 or influences > 16:
        raise SkinnedVolumeMeshError("max_influences must be between 1 and 16.")


def _prepare_source_meshes(
    skeleton: SkeletonAsset,
    meshes: Sequence[SkeletalMeshAsset],
) -> Dict[str, Any]:
    mesh_list = list(meshes or [])
    if not mesh_list:
        raise SkinnedVolumeMeshError("At least one skeletal mesh is required.")

    positions_all: List[np.ndarray] = []
    triangles_all: List[np.ndarray] = []
    vertex_influences: List[List[Tuple[int, float]]] = []
    mesh_names: List[str] = []
    vertex_offset = 0
    joint_count = len(skeleton.joints)

    for mesh in mesh_list:
        try:
            mesh.validate(None)
        except Exception as exc:
            raise SkinnedVolumeMeshError(f"Invalid source mesh {getattr(mesh, 'name', '')!r}: {exc}") from exc
        metadata = dict(getattr(mesh, "metadata", None) or {})
        raw_positions = metadata.get("bind_positions")
        positions = np.asarray(raw_positions, dtype="f4") if raw_positions is not None else np.zeros((0, 3), dtype="f4")
        if positions.ndim != 2 or positions.shape != (int(mesh.vertex_count), 3):
            raise SkinnedVolumeMeshError(
                f"Mesh {mesh.name!r} is missing valid metadata['bind_positions']."
            )
        triangles = np.asarray(mesh.triangle_indices, dtype=np.int32).reshape(-1, 3)
        if triangles.size == 0:
            continue

        local_influences: List[List[Tuple[int, float]]] = [
            [] for _ in range(int(mesh.vertex_count))
        ]
        for skin in mesh.vertex_skins:
            slot: List[Tuple[int, float]] = []
            for influence in skin.influences:
                joint_index = int(influence.joint_index)
                weight = float(influence.weight)
                if joint_index < 0 or weight <= 0.0:
                    continue
                if joint_index >= joint_count:
                    raise SkinnedVolumeMeshError(
                        f"Mesh {mesh.name!r} references joint {joint_index}, "
                        f"but skeleton {skeleton.name!r} has {joint_count} joints."
                    )
                slot.append((joint_index, weight))
            local_influences[int(skin.vertex_index)] = slot

        tri_positions = positions[triangles]
        cross = np.cross(tri_positions[:, 1] - tri_positions[:, 0], tri_positions[:, 2] - tri_positions[:, 0])
        valid = np.linalg.norm(cross, axis=1) > 1.0e-10
        triangles = triangles[valid]
        if triangles.size == 0:
            continue
        positions_all.append(positions)
        triangles_all.append(triangles + int(vertex_offset))
        vertex_influences.extend(local_influences)
        mesh_names.append(str(mesh.name))
        vertex_offset += int(positions.shape[0])

    if not positions_all or not triangles_all:
        raise SkinnedVolumeMeshError("Source meshes contain no non-degenerate triangles.")
    if not any(bool(slot) for slot in vertex_influences):
        raise SkinnedVolumeMeshError("Source meshes contain no transferable skin weights.")
    return {
        "positions": np.concatenate(positions_all, axis=0).astype("f4", copy=False),
        "triangles": np.concatenate(triangles_all, axis=0).astype(np.int32, copy=False),
        "vertex_influences": vertex_influences,
        "mesh_names": mesh_names,
    }


def _voxelize(
    vertices: np.ndarray,
    faces: np.ndarray,
    resolution: int,
    *,
    fill_interior: bool,
) -> Tuple[np.ndarray, np.ndarray, float]:
    try:
        import trimesh
    except Exception as exc:
        raise SkinnedVolumeMeshError("Volume remeshing requires the trimesh package.") from exc

    positions = np.asarray(vertices, dtype="f8").reshape(-1, 3)
    triangles = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    extents = positions.max(axis=0) - positions.min(axis=0)
    longest = float(np.max(extents))
    if not math.isfinite(longest) or longest <= 1.0e-8:
        raise SkinnedVolumeMeshError("Source mesh bounds have no usable volume.")
    voxel_size = longest / float(max(MIN_VOLUME_RESOLUTION, int(resolution)))
    estimated_shape = np.ceil(extents / float(voxel_size)).astype(np.int64) + 3
    estimated_cells = math.prod(int(value) for value in estimated_shape)
    if estimated_cells > _MAX_VOLUME_CELLS:
        raise SkinnedVolumeMeshError(
            f"Volume would need about {estimated_cells:,} cells; lower Volume Resolution."
        )

    try:
        source = trimesh.Trimesh(vertices=positions, faces=triangles, process=False)
        volume = source.voxelized(pitch=float(voxel_size), method="subdivide")
        if fill_interior:
            volume = volume.fill(method="holes")
        occupancy = np.asarray(volume.matrix, dtype=bool)
        origin_center = np.asarray(
            volume.indices_to_points(np.asarray([[0, 0, 0]], dtype=np.int64))[0],
            dtype="f8",
        )
    except Exception as exc:
        raise SkinnedVolumeMeshError(f"Mesh voxelization failed: {exc}") from exc

    if occupancy.ndim != 3 or occupancy.size <= 0 or not np.any(occupancy):
        raise SkinnedVolumeMeshError("Voxelization produced an empty volume.")
    if int(occupancy.size) > _MAX_VOLUME_CELLS:
        raise SkinnedVolumeMeshError(
            f"Volume needs {int(occupancy.size):,} cells; lower Volume Resolution."
        )
    lower_origin = origin_center - (float(voxel_size) * 0.5)
    return occupancy, lower_origin.astype("f4"), float(voxel_size)


def _mesh_occupancy(
    occupancy: np.ndarray,
    origin: np.ndarray,
    voxel_size: float,
) -> Tuple[np.ndarray, np.ndarray]:
    occupied = np.asarray(occupancy, dtype=bool)
    padded = np.pad(occupied, 1, mode="constant", constant_values=False)
    # Face corner order points out of the occupied cell.
    directions = (
        ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
        ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
        ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
        ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
        ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
        ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
    )
    vertex_map: Dict[Tuple[int, int, int], int] = {}
    vertex_keys: List[Tuple[int, int, int]] = []
    triangles: List[Tuple[int, int, int]] = []
    sx, sy, sz = occupied.shape

    for (dx, dy, dz), corners in directions:
        neighbor = padded[
            1 + dx : 1 + dx + sx,
            1 + dy : 1 + dy + sy,
            1 + dz : 1 + dz + sz,
        ]
        boundary_cells = np.argwhere(occupied & ~neighbor)
        for raw_cell in boundary_cells:
            cell = (int(raw_cell[0]), int(raw_cell[1]), int(raw_cell[2]))
            face_indices: List[int] = []
            for corner in corners:
                key = (
                    cell[0] + int(corner[0]),
                    cell[1] + int(corner[1]),
                    cell[2] + int(corner[2]),
                )
                index = vertex_map.get(key)
                if index is None:
                    index = len(vertex_keys)
                    vertex_map[key] = index
                    vertex_keys.append(key)
                face_indices.append(index)
            triangles.append((face_indices[0], face_indices[1], face_indices[2]))
            triangles.append((face_indices[0], face_indices[2], face_indices[3]))
            if len(triangles) > _MAX_OUTPUT_TRIANGLES:
                raise SkinnedVolumeMeshError(
                    f"Volume exceeds {_MAX_OUTPUT_TRIANGLES:,} triangles; lower Volume Resolution."
                )

    if not vertex_keys or not triangles:
        raise SkinnedVolumeMeshError("Volume surface extraction produced no mesh.")
    grid_vertices = np.asarray(vertex_keys, dtype="f4")
    positions = np.asarray(origin, dtype="f4").reshape(1, 3) + grid_vertices * np.float32(voxel_size)
    return positions.astype("f4", copy=False), np.asarray(triangles, dtype=np.int32)


def _taubin_smooth(
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    passes: int,
) -> np.ndarray:
    positions = np.asarray(vertices, dtype="f4").copy()
    triangles = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    if positions.shape[0] <= 3 or triangles.size == 0 or passes <= 0:
        return positions
    edges = np.concatenate(
        (triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]),
        axis=0,
    )
    edges = np.concatenate((edges, edges[:, ::-1]), axis=0)
    edges = np.unique(edges, axis=0)
    src = edges[:, 0]
    dst = edges[:, 1]
    degree = np.bincount(src, minlength=positions.shape[0]).astype("f4")
    degree[degree <= 0.0] = 1.0

    def step(current: np.ndarray, factor: float) -> np.ndarray:
        summed = np.zeros_like(current, dtype="f4")
        np.add.at(summed, src, current[dst])
        average = summed / degree[:, None]
        return current + np.float32(factor) * (average - current)

    for _ in range(int(passes)):
        positions = step(positions, 0.45)
        positions = step(positions, -0.47)
    return positions.astype("f4", copy=False)


def _transfer_skin_weights(
    output_positions: np.ndarray,
    source_positions: np.ndarray,
    source_triangles: np.ndarray,
    vertex_influences: Sequence[Sequence[Tuple[int, float]]],
    *,
    max_influences: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    try:
        import trimesh
        from scipy.spatial import cKDTree
    except Exception as exc:
        raise SkinnedVolumeMeshError("Skin transfer requires trimesh and scipy.") from exc

    points = np.asarray(output_positions, dtype="f8").reshape(-1, 3)
    source_vertices = np.asarray(source_positions, dtype="f8").reshape(-1, 3)
    faces = np.asarray(source_triangles, dtype=np.int64).reshape(-1, 3)
    tri_positions = source_vertices[faces]
    centroids = tri_positions.mean(axis=1)
    candidate_count = min(12, int(faces.shape[0]))
    if candidate_count <= 0:
        raise SkinnedVolumeMeshError("Source mesh has no triangles for skin transfer.")
    tree = cKDTree(centroids)

    selected_faces = np.zeros((points.shape[0],), dtype=np.int32)
    barycentric = np.zeros((points.shape[0], 3), dtype="f4")
    batch_size = 2048
    for start in range(0, int(points.shape[0]), batch_size):
        end = min(int(points.shape[0]), start + batch_size)
        batch = points[start:end]
        _distance, candidate_ids = tree.query(batch, k=candidate_count)
        candidate_ids = np.asarray(candidate_ids, dtype=np.int64)
        if candidate_ids.ndim == 1:
            candidate_ids = candidate_ids[:, None]
        candidates = tri_positions[candidate_ids.reshape(-1)]
        repeated = np.repeat(batch, candidate_ids.shape[1], axis=0)
        closest = trimesh.triangles.closest_point(candidates, repeated)
        distances_sq = np.sum((closest - repeated) ** 2, axis=1).reshape(batch.shape[0], -1)
        best_slot = np.argmin(distances_sq, axis=1)
        row = np.arange(batch.shape[0])
        best_face = candidate_ids[row, best_slot]
        best_triangles = tri_positions[best_face]
        best_points = closest.reshape(batch.shape[0], candidate_ids.shape[1], 3)[row, best_slot]
        best_bary = trimesh.triangles.points_to_barycentric(
            best_triangles,
            best_points,
            method="cross",
        )
        best_bary = np.clip(np.asarray(best_bary, dtype="f8"), 0.0, 1.0)
        totals = best_bary.sum(axis=1, keepdims=True)
        totals[totals <= 1.0e-12] = 1.0
        best_bary = best_bary / totals
        selected_faces[start:end] = best_face.astype(np.int32, copy=False)
        barycentric[start:end] = best_bary.astype("f4", copy=False)

    joint_indices = np.zeros((points.shape[0], max_influences), dtype=np.uint16)
    joint_weights = np.zeros((points.shape[0], max_influences), dtype="f4")
    weighted_vertex_ids = np.asarray(
        [index for index, slot in enumerate(vertex_influences) if slot],
        dtype=np.int64,
    )
    weighted_tree = cKDTree(source_vertices[weighted_vertex_ids]) if weighted_vertex_ids.size else None

    for vertex_index in range(int(points.shape[0])):
        face_index = int(selected_faces[vertex_index])
        influences = transfer_barycentric_skin_weights(
            vertex_influences,
            faces[face_index],
            barycentric[vertex_index],
            max_influences=max_influences,
        )
        if not influences and weighted_tree is not None:
            _distance, nearest_slot = weighted_tree.query(points[vertex_index], k=1)
            source_vertex = int(weighted_vertex_ids[int(nearest_slot)])
            influences = sorted(
                [(int(joint), float(weight)) for joint, weight in vertex_influences[source_vertex]],
                key=lambda item: (-item[1], item[0]),
            )[:max_influences]
            total = sum(weight for _joint, weight in influences)
            if total > 0.0:
                influences = [(joint, weight / total) for joint, weight in influences]
        if not influences:
            raise SkinnedVolumeMeshError("A generated vertex could not receive skin weights.")
        for slot, (joint, weight) in enumerate(influences):
            joint_indices[vertex_index, slot] = np.uint16(joint)
            joint_weights[vertex_index, slot] = np.float32(weight)

    return joint_indices, joint_weights, selected_faces, barycentric


def _mesh_asset_from_skin_arrays(
    skeleton: SkeletonAsset,
    positions: np.ndarray,
    faces: np.ndarray,
    joint_indices: np.ndarray,
    joint_weights: np.ndarray,
    *,
    name: str,
) -> SkeletalMeshAsset:
    bind_positions = np.asarray(positions, dtype="f4").reshape(-1, 3)
    triangles = np.asarray(faces, dtype=np.int32).reshape(-1, 3)
    joints = np.asarray(joint_indices, dtype=np.int32)
    weights = np.asarray(joint_weights, dtype="f4")
    skins: List[VertexSkin] = []
    for vertex_index in range(int(bind_positions.shape[0])):
        influences: List[VertexInfluence] = []
        for joint, weight in zip(joints[vertex_index], weights[vertex_index]):
            if int(joint) < 0 or float(weight) <= 0.0:
                continue
            influences.append(
                VertexInfluence(joint_index=int(joint), weight=float(weight))
            )
        total = sum(float(influence.weight) for influence in influences)
        if total <= 0.0:
            raise SkinnedVolumeMeshError("Generated mesh contains an unweighted vertex.")
        for influence in influences:
            influence.weight = float(influence.weight) / total
        skins.append(VertexSkin(vertex_index=vertex_index, influences=influences))

    asset = SkeletalMeshAsset(
        name=str(name or "SkinnedVolumeMesh"),
        skeleton_name=str(skeleton.name),
        vertex_count=int(bind_positions.shape[0]),
        triangle_indices=triangles.reshape(-1).astype(int).tolist(),
        vertex_skins=skins,
        metadata={
            "bind_positions": bind_positions.astype(float).tolist(),
            "generated_by": _SCHEMA,
            "collision_proxy": True,
        },
    )
    asset.validate(skeleton)
    return asset


def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return stem or "SkinnedVolumeMesh"


__all__ = [
    "MAX_REMESH_QUALITY",
    "MAX_VOLUME_RESOLUTION",
    "MIN_REMESH_QUALITY",
    "MIN_VOLUME_RESOLUTION",
    "SkinnedVolumeMeshArrays",
    "SkinnedVolumeMeshError",
    "SkinnedVolumeMeshResult",
    "SkinnedVolumeMeshSettings",
    "build_skinned_volume_mesh",
    "build_skinned_volume_mesh_arrays",
    "build_skinned_volume_mesh_from_rig_context",
    "load_skinned_volume_mesh_asset",
    "mesh_asset_from_arrays",
    "write_obj",
    "write_skin_npz",
    "write_volume_npz",
]
