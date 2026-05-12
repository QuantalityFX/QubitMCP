from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from .fbx_canonical import SkeletalMeshAsset, SkeletonAsset


C0 = 0.28209479177387814
_EPSILON = 1.0e-8
_SCHEMA = "qubit.skinned_splat_proxy.v1"


class SkinnedSplatProxyError(RuntimeError):
    """Raised when a skinned splat proxy cannot be generated."""


@dataclass(frozen=True)
class SkinnedSplatProxySettings:
    sample_count: int = 50_000
    max_influences: int = 4
    seed: int = 1234
    color_mode: str = "skin_weights"
    opacity: float = 1.0
    radius_scale: float = 1.0
    adaptive_radius: bool = True
    min_radius: float = 1.0e-4
    normal_axis_scale: float = 0.35
    hash_source_fbx: bool = False


@dataclass(frozen=True)
class SkinnedSplatProxyArrays:
    splats: np.ndarray
    bind_positions: np.ndarray
    bind_quats: np.ndarray
    bind_radius_scale: np.ndarray
    joint_indices: np.ndarray
    joint_weights: np.ndarray
    source_mesh_index: np.ndarray
    source_triangle_index: np.ndarray
    source_vertex_indices: np.ndarray
    source_barycentric: np.ndarray
    debug_color_rgba: np.ndarray
    mesh_names: Tuple[str, ...]
    joint_names: Tuple[str, ...]
    total_source_area: float


@dataclass(frozen=True)
class SkinnedSplatProxyResult:
    manifest_path: Path
    splat_ply_path: Path
    skin_npz_path: Path
    arrays: SkinnedSplatProxyArrays
    manifest: Dict[str, Any]


def joint_debug_color(joint_index: int) -> Tuple[float, float, float]:
    idx = int(max(0, joint_index)) + 1
    r = 0.20 + 0.80 * (abs(math.sin(float(idx) * 12.9898 + 78.233)) % 1.0)
    g = 0.20 + 0.80 * (abs(math.sin(float(idx) * 39.3468 + 11.135)) % 1.0)
    b = 0.20 + 0.80 * (abs(math.sin(float(idx) * 73.1563 + 47.853)) % 1.0)
    return (float(r), float(g), float(b))


def transfer_barycentric_skin_weights(
    vertex_influences: Sequence[Sequence[Tuple[int, float]]],
    triangle_vertex_indices: Sequence[int],
    barycentric: Sequence[float],
    *,
    max_influences: int = 4,
) -> List[Tuple[int, float]]:
    if max_influences <= 0:
        raise SkinnedSplatProxyError("max_influences must be greater than zero.")
    if len(triangle_vertex_indices) != 3:
        raise SkinnedSplatProxyError("triangle_vertex_indices must contain three indices.")
    if len(barycentric) != 3:
        raise SkinnedSplatProxyError("barycentric must contain three weights.")

    merged: Dict[int, float] = {}
    for corner, raw_vertex_index in enumerate(triangle_vertex_indices):
        vertex_index = int(raw_vertex_index)
        if vertex_index < 0 or vertex_index >= len(vertex_influences):
            continue
        corner_weight = float(barycentric[corner])
        if corner_weight <= 0.0:
            continue
        for raw_joint, raw_weight in vertex_influences[vertex_index]:
            joint_index = int(raw_joint)
            weight = float(raw_weight) * corner_weight
            if joint_index < 0 or weight <= 0.0:
                continue
            merged[joint_index] = float(merged.get(joint_index, 0.0)) + float(weight)

    packed = [(joint_index, weight) for joint_index, weight in merged.items() if weight > 0.0]
    packed.sort(key=lambda item: (-float(item[1]), int(item[0])))
    if len(packed) > max_influences:
        packed = packed[:max_influences]
    total = sum(float(weight) for _, weight in packed)
    if total <= 0.0:
        return []
    return [(int(joint), float(weight) / float(total)) for joint, weight in packed]


def build_skinned_splat_proxy_arrays(
    skeleton: SkeletonAsset,
    meshes: Sequence[SkeletalMeshAsset],
    settings: SkinnedSplatProxySettings | None = None,
) -> SkinnedSplatProxyArrays:
    settings = settings or SkinnedSplatProxySettings()
    _validate_settings(settings)
    skeleton.validate()
    mesh_list = list(meshes or [])
    if not mesh_list:
        raise SkinnedSplatProxyError("At least one skeletal mesh is required.")

    joint_count = len(skeleton.joints)
    if joint_count <= 0:
        raise SkinnedSplatProxyError("Skeleton must contain at least one joint.")
    if joint_count > 65535:
        raise SkinnedSplatProxyError("Skinned splat proxy currently supports up to 65535 joints.")

    prepared_meshes = [_prepare_mesh(mesh, skeleton) for mesh in mesh_list]
    candidates = _collect_candidate_triangles(prepared_meshes)
    if int(candidates["areas"].shape[0]) <= 0:
        raise SkinnedSplatProxyError("No weighted, non-degenerate mesh triangles were found.")

    sample_count = int(settings.sample_count)
    rng = np.random.default_rng(int(settings.seed))
    areas = candidates["areas"].astype(np.float64, copy=False)
    total_area = float(areas.sum())
    if not math.isfinite(total_area) or total_area <= 0.0:
        raise SkinnedSplatProxyError("Mesh triangle area is zero.")

    selected = rng.choice(
        int(areas.shape[0]),
        size=sample_count,
        replace=True,
        p=(areas / total_area),
    )
    bary = _sample_barycentric(rng, sample_count)

    tri_positions = candidates["positions"][selected]
    positions = (
        tri_positions[:, 0, :] * bary[:, 0:1]
        + tri_positions[:, 1, :] * bary[:, 1:2]
        + tri_positions[:, 2, :] * bary[:, 2:3]
    ).astype("f4", copy=False)

    normals = candidates["normals"][selected].astype("f4", copy=False)
    quats = _quats_from_z_to_normals(normals)

    radii = _sample_radii(
        settings,
        total_area=total_area,
        sample_count=sample_count,
        selected_areas=candidates["areas"][selected],
        selected_feature_radii=candidates["feature_radii"][selected],
    )
    scale3 = _normalized_axis_scale(float(settings.normal_axis_scale))
    scales = np.tile(scale3.reshape(1, 3), (sample_count, 1)).astype("f4", copy=False)

    max_influences = int(settings.max_influences)
    joint_indices = np.zeros((sample_count, max_influences), dtype=np.uint16)
    joint_weights = np.zeros((sample_count, max_influences), dtype="f4")

    selected_mesh_indices = candidates["mesh_indices"][selected].astype(np.int32, copy=False)
    selected_triangle_indices = candidates["triangle_indices"][selected].astype(np.int32, copy=False)
    selected_vertex_indices = candidates["vertex_indices"][selected].astype(np.int32, copy=False)

    for sample_index in range(sample_count):
        mesh_index = int(selected_mesh_indices[sample_index])
        prepared = prepared_meshes[mesh_index]
        influences = transfer_barycentric_skin_weights(
            prepared["vertex_influences"],
            selected_vertex_indices[sample_index],
            bary[sample_index],
            max_influences=max_influences,
        )
        if not influences:
            raise SkinnedSplatProxyError(
                "Sampled a triangle without transferable skin weights; candidate filtering failed."
            )
        for slot, (joint_index, weight) in enumerate(influences):
            joint_indices[sample_index, slot] = np.uint16(joint_index)
            joint_weights[sample_index, slot] = np.float32(weight)

    color_mode = str(settings.color_mode or "skin_weights").strip().lower()
    if color_mode not in {"skin_weights", "neutral"}:
        raise SkinnedSplatProxyError(f"Unsupported skinned splat color mode: {settings.color_mode!r}.")
    if color_mode == "skin_weights":
        rgb = _weight_debug_colors(joint_indices, joint_weights, joint_count)
    else:
        rgb = np.full((sample_count, 3), (0.78, 0.78, 0.78), dtype="f4")

    alpha = max(0.0, min(1.0, float(settings.opacity)))
    rgba = np.ones((sample_count, 4), dtype="f4")
    rgba[:, :3] = rgb
    rgba[:, 3] = np.float32(alpha)

    splats = np.concatenate([positions, rgba, radii, scales, quats], axis=1).astype(
        "f4", copy=False
    )
    bind_radius_scale = np.concatenate([radii, scales], axis=1).astype("f4", copy=False)

    return SkinnedSplatProxyArrays(
        splats=splats,
        bind_positions=positions,
        bind_quats=quats,
        bind_radius_scale=bind_radius_scale,
        joint_indices=joint_indices,
        joint_weights=joint_weights,
        source_mesh_index=selected_mesh_indices,
        source_triangle_index=selected_triangle_indices,
        source_vertex_indices=selected_vertex_indices,
        source_barycentric=bary.astype("f4", copy=False),
        debug_color_rgba=rgba,
        mesh_names=tuple(str(mesh.name) for mesh in mesh_list),
        joint_names=tuple(str(joint.name) for joint in skeleton.joints),
        total_source_area=total_area,
    )


def write_gaussian_splat_ply(path: str | Path, splats: np.ndarray) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(splats, dtype="f4")
    if arr.ndim != 2 or int(arr.shape[1]) != 15:
        raise SkinnedSplatProxyError("Gaussian splat PLY writer expects an Nx15 array.")

    rgb = np.clip(arr[:, 3:6], 0.0, 1.0)
    alpha = np.clip(arr[:, 6], 1.0e-6, 1.0 - 1.0e-6)
    radius = np.maximum(arr[:, 7], 1.0e-8)
    scale3 = np.maximum(arr[:, 8:11], 1.0e-8)
    quat_xyzw = _normalize_quats(arr[:, 11:15])

    fdc = (rgb - 0.5) / C0
    opacity = np.log(alpha / (1.0 - alpha)).reshape(-1, 1)
    axes = np.maximum(radius.reshape(-1, 1) * scale3, 1.0e-8)
    log_scales = np.log(axes)
    rot_wxyz = np.column_stack(
        [quat_xyzw[:, 3], quat_xyzw[:, 0], quat_xyzw[:, 1], quat_xyzw[:, 2]]
    )

    payload = np.concatenate(
        [arr[:, 0:3], fdc, opacity, log_scales, rot_wxyz],
        axis=1,
    ).astype("<f4", copy=False)
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "comment generated_by QubitMCP skinned_splat_proxy\n"
        f"element vertex {int(payload.shape[0])}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property float f_dc_0\n"
        "property float f_dc_1\n"
        "property float f_dc_2\n"
        "property float opacity\n"
        "property float scale_0\n"
        "property float scale_1\n"
        "property float scale_2\n"
        "property float rot_0\n"
        "property float rot_1\n"
        "property float rot_2\n"
        "property float rot_3\n"
        "end_header\n"
    )
    with out_path.open("wb") as handle:
        handle.write(header.encode("ascii"))
        handle.write(payload.tobytes(order="C"))
    return out_path


def write_skinned_splat_skin_npz(path: str | Path, arrays: SkinnedSplatProxyArrays) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        schema=np.array(_SCHEMA),
        bind_positions=arrays.bind_positions.astype("f4", copy=False),
        bind_quats=arrays.bind_quats.astype("f4", copy=False),
        bind_radius_scale=arrays.bind_radius_scale.astype("f4", copy=False),
        joint_indices=arrays.joint_indices.astype(np.uint16, copy=False),
        joint_weights=arrays.joint_weights.astype("f4", copy=False),
        source_mesh_index=arrays.source_mesh_index.astype(np.int32, copy=False),
        source_triangle_index=arrays.source_triangle_index.astype(np.int32, copy=False),
        source_vertex_indices=arrays.source_vertex_indices.astype(np.int32, copy=False),
        source_barycentric=arrays.source_barycentric.astype("f4", copy=False),
        debug_color_rgba=arrays.debug_color_rgba.astype("f4", copy=False),
    )
    return out_path


def build_skinned_splat_proxy(
    skeleton: SkeletonAsset,
    meshes: Sequence[SkeletalMeshAsset],
    output_dir: str | Path,
    *,
    asset_name: str = "SkinnedSplatProxy",
    source_fbx: str = "",
    settings: SkinnedSplatProxySettings | None = None,
) -> SkinnedSplatProxyResult:
    settings = settings or SkinnedSplatProxySettings()
    arrays = build_skinned_splat_proxy_arrays(skeleton, meshes, settings=settings)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(asset_name)
    splat_ply_path = out_dir / f"{stem}.ply"
    skin_npz_path = out_dir / f"{stem}.skin.npz"
    manifest_path = out_dir / f"{stem}.qskinned_splat.json"

    write_gaussian_splat_ply(splat_ply_path, arrays.splats)
    write_skinned_splat_skin_npz(skin_npz_path, arrays)

    source_hash = ""
    if settings.hash_source_fbx and source_fbx:
        source_hash = _sha256_file_if_available(Path(source_fbx))

    manifest = {
        "schema": _SCHEMA,
        "splat_ply": _relative_or_name(splat_ply_path, manifest_path.parent),
        "skin_npz": _relative_or_name(skin_npz_path, manifest_path.parent),
        "source_fbx": str(source_fbx or ""),
        "source_fbx_hash": source_hash,
        "skeleton_name": str(skeleton.name),
        "joint_names": list(arrays.joint_names),
        "mesh_names": list(arrays.mesh_names),
        "sample_count": int(arrays.splats.shape[0]),
        "max_influences": int(settings.max_influences),
        "color_mode": str(settings.color_mode),
        "total_source_area": float(arrays.total_source_area),
        "generator": {
            "method": "surface_area_barycentric",
            "settings": asdict(settings),
        },
        "arrays": {
            "splats": list(arrays.splats.shape),
            "joint_indices": list(arrays.joint_indices.shape),
            "joint_weights": list(arrays.joint_weights.shape),
        },
    }
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return SkinnedSplatProxyResult(
        manifest_path=manifest_path,
        splat_ply_path=splat_ply_path,
        skin_npz_path=skin_npz_path,
        arrays=arrays,
        manifest=manifest,
    )


def build_skinned_splat_proxy_from_rig_context(
    rig_context: Dict[str, Any],
    output_dir: str | Path,
    *,
    asset_name: str = "SkinnedSplatProxy",
    source_fbx: str = "",
    settings: SkinnedSplatProxySettings | None = None,
) -> SkinnedSplatProxyResult:
    skeleton = rig_context.get("skeleton") if isinstance(rig_context, dict) else None
    meshes = rig_context.get("meshes") if isinstance(rig_context, dict) else None
    if not isinstance(skeleton, SkeletonAsset):
        raise SkinnedSplatProxyError("rig_context does not contain a SkeletonAsset.")
    if not isinstance(meshes, Sequence) or isinstance(meshes, (str, bytes)):
        raise SkinnedSplatProxyError("rig_context does not contain skeletal meshes.")
    return build_skinned_splat_proxy(
        skeleton,
        meshes,
        output_dir,
        asset_name=asset_name,
        source_fbx=source_fbx,
        settings=settings,
    )


def _validate_settings(settings: SkinnedSplatProxySettings) -> None:
    if int(settings.sample_count) <= 0:
        raise SkinnedSplatProxyError("sample_count must be greater than zero.")
    if int(settings.max_influences) <= 0:
        raise SkinnedSplatProxyError("max_influences must be greater than zero.")
    if int(settings.max_influences) > 16:
        raise SkinnedSplatProxyError("max_influences above 16 is not supported for proxies.")
    if float(settings.radius_scale) <= 0.0:
        raise SkinnedSplatProxyError("radius_scale must be greater than zero.")
    if float(settings.min_radius) <= 0.0:
        raise SkinnedSplatProxyError("min_radius must be greater than zero.")
    if float(settings.normal_axis_scale) <= 0.0:
        raise SkinnedSplatProxyError("normal_axis_scale must be greater than zero.")


def _prepare_mesh(mesh: SkeletalMeshAsset, skeleton: SkeletonAsset) -> Dict[str, Any]:
    # Retargeted assets can pair the original mesh with a posed/retargeted
    # skeleton object whose name no longer matches mesh.skeleton_name. The
    # joint index order is the contract the proxy needs, so validate the mesh
    # structure independently and then verify the indices fit this skeleton.
    mesh.validate(None)
    skeleton_joint_count = len(getattr(skeleton, "joints", []) or [])
    max_joint_index = -1
    for skin in mesh.vertex_skins:
        for influence in skin.influences:
            max_joint_index = max(max_joint_index, int(influence.joint_index))
    if max_joint_index >= skeleton_joint_count:
        raise SkinnedSplatProxyError(
            f"Mesh {mesh.name!r} references joint {max_joint_index}, "
            f"but skeleton {skeleton.name!r} only has {skeleton_joint_count} joints."
        )
    positions = _mesh_bind_positions(mesh)
    triangles = np.asarray(mesh.triangle_indices, dtype=np.int32).reshape(-1, 3)
    vertex_influences: List[List[Tuple[int, float]]] = [[] for _ in range(int(mesh.vertex_count))]
    for skin in mesh.vertex_skins:
        slot: List[Tuple[int, float]] = []
        for influence in skin.influences:
            slot.append((int(influence.joint_index), float(influence.weight)))
        vertex_influences[int(skin.vertex_index)] = slot
    return {
        "mesh": mesh,
        "positions": positions,
        "triangles": triangles,
        "vertex_influences": vertex_influences,
    }


def _mesh_bind_positions(mesh: SkeletalMeshAsset) -> np.ndarray:
    metadata = dict(getattr(mesh, "metadata", None) or {})
    raw_positions = metadata.get("bind_positions")
    if raw_positions is None:
        raise SkinnedSplatProxyError(
            f"Mesh {mesh.name!r} is missing metadata['bind_positions']."
        )
    positions = np.asarray(raw_positions, dtype="f4")
    if positions.ndim != 2 or int(positions.shape[1]) != 3:
        raise SkinnedSplatProxyError(
            f"Mesh {mesh.name!r} bind_positions must be an Nx3 array."
        )
    if int(positions.shape[0]) != int(mesh.vertex_count):
        raise SkinnedSplatProxyError(
            f"Mesh {mesh.name!r} bind_positions count does not match vertex_count."
        )
    return positions


def _collect_candidate_triangles(prepared_meshes: Sequence[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    mesh_indices: List[int] = []
    triangle_indices: List[int] = []
    vertex_indices: List[Tuple[int, int, int]] = []
    positions: List[np.ndarray] = []
    normals: List[np.ndarray] = []
    areas: List[float] = []
    feature_radii: List[float] = []

    for mesh_index, prepared in enumerate(prepared_meshes):
        mesh_positions = np.asarray(prepared["positions"], dtype="f4")
        triangles = np.asarray(prepared["triangles"], dtype=np.int32)
        vertex_influences = prepared["vertex_influences"]
        for triangle_index, tri in enumerate(triangles):
            tri_tuple = (int(tri[0]), int(tri[1]), int(tri[2]))
            if min(tri_tuple) < 0 or max(tri_tuple) >= int(mesh_positions.shape[0]):
                continue
            if not any(vertex_influences[vertex_index] for vertex_index in tri_tuple):
                continue
            p0, p1, p2 = mesh_positions[list(tri_tuple)]
            normal_raw = np.cross(p1 - p0, p2 - p0)
            norm = float(np.linalg.norm(normal_raw))
            area = 0.5 * norm
            if not math.isfinite(area) or area <= _EPSILON:
                continue
            local_radius = _triangle_feature_radius(p0, p1, p2, area)
            normal = (normal_raw / norm).astype("f4", copy=False)
            mesh_indices.append(int(mesh_index))
            triangle_indices.append(int(triangle_index))
            vertex_indices.append(tri_tuple)
            positions.append(np.stack([p0, p1, p2], axis=0).astype("f4", copy=False))
            normals.append(normal)
            areas.append(float(area))
            feature_radii.append(float(local_radius))

    if not areas:
        return {
            "mesh_indices": np.zeros((0,), dtype=np.int32),
            "triangle_indices": np.zeros((0,), dtype=np.int32),
            "vertex_indices": np.zeros((0, 3), dtype=np.int32),
            "positions": np.zeros((0, 3, 3), dtype="f4"),
            "normals": np.zeros((0, 3), dtype="f4"),
            "areas": np.zeros((0,), dtype="f4"),
            "feature_radii": np.zeros((0,), dtype="f4"),
        }

    return {
        "mesh_indices": np.asarray(mesh_indices, dtype=np.int32),
        "triangle_indices": np.asarray(triangle_indices, dtype=np.int32),
        "vertex_indices": np.asarray(vertex_indices, dtype=np.int32),
        "positions": np.asarray(positions, dtype="f4"),
        "normals": np.asarray(normals, dtype="f4"),
        "areas": np.asarray(areas, dtype="f4"),
        "feature_radii": np.asarray(feature_radii, dtype="f4"),
    }


def _sample_radii(
    settings: SkinnedSplatProxySettings,
    *,
    total_area: float,
    sample_count: int,
    selected_areas: np.ndarray,
    selected_feature_radii: np.ndarray,
) -> np.ndarray:
    base_radius = max(
        float(settings.min_radius),
        math.sqrt(max(float(total_area), _EPSILON) / (float(sample_count) * math.pi))
        * float(settings.radius_scale),
    )
    count = int(np.asarray(selected_areas).reshape(-1).shape[0])
    if count <= 0:
        return np.zeros((0, 1), dtype="f4")
    if not bool(settings.adaptive_radius):
        return np.full((count, 1), float(base_radius), dtype="f4")

    areas = np.maximum(np.asarray(selected_areas, dtype="f4").reshape(-1), np.float32(_EPSILON))
    expected_samples = np.maximum(
        (float(sample_count) * areas) / max(float(total_area), _EPSILON),
        np.float32(1.0),
    )
    density_radius = np.sqrt(areas / (expected_samples * np.float32(math.pi))).astype("f4")
    density_radius *= np.float32(float(settings.radius_scale))

    feature_radius = np.maximum(
        np.asarray(selected_feature_radii, dtype="f4").reshape(-1)
        * np.float32(float(settings.radius_scale)),
        np.float32(float(settings.min_radius)),
    )
    radii = np.minimum(
        np.full((count,), float(base_radius), dtype="f4"),
        np.minimum(density_radius, feature_radius),
    )
    radii = np.maximum(radii, np.float32(float(settings.min_radius)))
    return radii.reshape(-1, 1).astype("f4", copy=False)


def _triangle_feature_radius(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, area: float) -> float:
    edges = [
        float(np.linalg.norm(p1 - p0)),
        float(np.linalg.norm(p2 - p1)),
        float(np.linalg.norm(p0 - p2)),
    ]
    valid_edges = [edge for edge in edges if math.isfinite(edge) and edge > _EPSILON]
    if not valid_edges:
        return 1.0
    altitudes = [(2.0 * float(area)) / edge for edge in valid_edges]
    valid_altitudes = [alt for alt in altitudes if math.isfinite(alt) and alt > _EPSILON]
    local_extent = min(valid_edges + valid_altitudes) if valid_altitudes else min(valid_edges)
    return max(_EPSILON, 0.45 * float(local_extent))


def _sample_barycentric(rng: np.random.Generator, count: int) -> np.ndarray:
    r1 = rng.random(int(count), dtype=np.float32)
    r2 = rng.random(int(count), dtype=np.float32)
    sqrt_r1 = np.sqrt(r1).astype("f4", copy=False)
    b0 = 1.0 - sqrt_r1
    b1 = sqrt_r1 * (1.0 - r2)
    b2 = sqrt_r1 * r2
    return np.stack([b0, b1, b2], axis=1).astype("f4", copy=False)


def _normalized_axis_scale(normal_axis_scale: float) -> np.ndarray:
    normal_scale = max(float(normal_axis_scale), 1.0e-6)
    tangent_scale = 1.0
    geometric_mean = (tangent_scale * tangent_scale * normal_scale) ** (1.0 / 3.0)
    return np.asarray(
        [
            tangent_scale / geometric_mean,
            tangent_scale / geometric_mean,
            normal_scale / geometric_mean,
        ],
        dtype="f4",
    )


def _quats_from_z_to_normals(normals: np.ndarray) -> np.ndarray:
    n = np.asarray(normals, dtype="f4").reshape(-1, 3)
    out = np.zeros((n.shape[0], 4), dtype="f4")
    z_axis = np.asarray([0.0, 0.0, 1.0], dtype="f4")
    for idx, normal in enumerate(n):
        length = float(np.linalg.norm(normal))
        if length <= _EPSILON:
            out[idx] = (0.0, 0.0, 0.0, 1.0)
            continue
        target = normal / np.float32(length)
        dot = float(np.dot(z_axis, target))
        if dot >= 1.0 - 1.0e-6:
            out[idx] = (0.0, 0.0, 0.0, 1.0)
        elif dot <= -1.0 + 1.0e-6:
            out[idx] = (1.0, 0.0, 0.0, 0.0)
        else:
            axis = np.cross(z_axis, target)
            axis_len = float(np.linalg.norm(axis))
            if axis_len <= _EPSILON:
                out[idx] = (0.0, 0.0, 0.0, 1.0)
                continue
            axis = axis / np.float32(axis_len)
            angle = math.acos(max(-1.0, min(1.0, dot)))
            half = angle * 0.5
            s = math.sin(half)
            out[idx] = (
                float(axis[0] * s),
                float(axis[1] * s),
                float(axis[2] * s),
                float(math.cos(half)),
            )
    return _normalize_quats(out)


def _normalize_quats(quats: np.ndarray) -> np.ndarray:
    q = np.asarray(quats, dtype="f4").reshape(-1, 4).copy()
    lengths = np.linalg.norm(q, axis=1, keepdims=True).astype("f4")
    valid = lengths[:, 0] > _EPSILON
    q[valid] = q[valid] / lengths[valid]
    q[~valid] = np.asarray((0.0, 0.0, 0.0, 1.0), dtype="f4")
    return q.astype("f4", copy=False)


def _weight_debug_colors(
    joint_indices: np.ndarray,
    joint_weights: np.ndarray,
    joint_count: int,
) -> np.ndarray:
    ji = np.asarray(joint_indices, dtype=np.int64)
    jw = np.asarray(joint_weights, dtype="f4")
    if ji.ndim != 2 or jw.ndim != 2 or ji.shape != jw.shape:
        raise SkinnedSplatProxyError("joint_indices and joint_weights must be matching 2D arrays.")
    palette_count = max(1, int(joint_count))
    palette = np.zeros((palette_count, 3), dtype="f4")
    for joint_index in range(palette_count):
        palette[joint_index] = np.asarray(joint_debug_color(joint_index), dtype="f4")
    colors = np.zeros((ji.shape[0], 3), dtype="f4")
    for slot in range(ji.shape[1]):
        joints = ji[:, slot]
        weights = jw[:, slot]
        valid = (joints >= 0) & (joints < palette_count) & (weights > 1.0e-8)
        if np.any(valid):
            colors[valid] += palette[joints[valid]] * weights[valid, None]
    return np.clip(colors, 0.0, 1.0).astype("f4", copy=False)


def _safe_stem(name: str) -> str:
    raw = str(name or "").strip() or "SkinnedSplatProxy"
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in raw)
    safe = safe.strip("._")
    return safe or "SkinnedSplatProxy"


def _relative_or_name(path: Path, base_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(base_dir.resolve())).replace("\\", "/")
    except Exception:
        return path.name


def _sha256_file_if_available(path: Path) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return ""


__all__ = [
    "SkinnedSplatProxyArrays",
    "SkinnedSplatProxyError",
    "SkinnedSplatProxyResult",
    "SkinnedSplatProxySettings",
    "build_skinned_splat_proxy",
    "build_skinned_splat_proxy_arrays",
    "build_skinned_splat_proxy_from_rig_context",
    "joint_debug_color",
    "transfer_barycentric_skin_weights",
    "write_gaussian_splat_ply",
    "write_skinned_splat_skin_npz",
]
