from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Tuple
import math

from .fbx_canonical import (
    Joint,
    JointTransform,
    SkeletalMeshAsset,
    SkeletonAsset,
    VertexInfluence,
    VertexSkin,
)

_IDENTITY_MATRIX_4X4: Tuple[float, ...] = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)

_BIND_INGEST_CACHE_MAX = 8
_BIND_INGEST_CACHE: "OrderedDict[Tuple[str, int, int, str, int], FBXBindIngestResult]" = OrderedDict()


class FBXBindIngestError(RuntimeError):
    """Raised when deterministic FBX bind ingest fails."""


@dataclass(eq=True)
class FBXBindIngestResult:
    source_path: str
    skeleton: SkeletonAsset
    meshes: List[SkeletalMeshAsset] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass(eq=True)
class SkeletonCompatibilityReport:
    compatible: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class _NodeRecord:
    idx: int
    name: str
    parent_idx: int | None
    transform: Tuple[float, ...]
    node_obj: Any


@dataclass
class _SimpleWeight:
    vertexid: int
    weight: float


@dataclass
class _SimpleBone:
    name: str
    weights: List[_SimpleWeight] = field(default_factory=list)
    offsetmatrix: Tuple[float, ...] = _IDENTITY_MATRIX_4X4


@dataclass
class _SimpleMesh:
    name: str
    vertices: List[Tuple[float, float, float]] = field(default_factory=list)
    faces: List[List[int]] = field(default_factory=list)
    bones: List[_SimpleBone] = field(default_factory=list)


@dataclass
class _SimpleNode:
    name: str
    children: List[Any] = field(default_factory=list)
    transformation: Tuple[float, ...] = _IDENTITY_MATRIX_4X4


@dataclass
class _SimpleScene:
    rootnode: _SimpleNode
    meshes: List[_SimpleMesh] = field(default_factory=list)


def _safe_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _unique_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _matrix4_from_obj(value: Any) -> Tuple[float, ...]:
    if value is None:
        return _IDENTITY_MATRIX_4X4

    # assimp matrix layout (a1..d4)
    keys = (
        "a1", "a2", "a3", "a4",
        "b1", "b2", "b3", "b4",
        "c1", "c2", "c3", "c4",
        "d1", "d2", "d3", "d4",
    )
    if all(hasattr(value, key) for key in keys):
        return tuple(_to_float(getattr(value, key)) for key in keys)
    if isinstance(value, dict) and all(key in value for key in keys):
        return tuple(_to_float(value[key]) for key in keys)

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            value = tolist()
        except Exception:
            pass

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        try:
            if len(value) == 16:
                return tuple(_to_float(v) for v in value)  # type: ignore[arg-type]
            if len(value) == 4 and all(
                isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and len(row) >= 4  # type: ignore[arg-type]
                for row in value
            ):
                out: List[float] = []
                for row in value:  # type: ignore[assignment]
                    out.extend(_to_float(row[i]) for i in range(4))  # type: ignore[index]
                return tuple(out)
        except Exception:
            pass

    try:
        out = [_to_float(value[r][c]) for r in range(4) for c in range(4)]  # type: ignore[index]
        if len(out) == 16:
            return tuple(out)
    except Exception:
        pass

    return _IDENTITY_MATRIX_4X4


def _quat_from_rotation_matrix(m00, m01, m02, m10, m11, m12, m20, m21, m22):
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(max(0.0, 1.0 + m00 - m11 - m22)) * 2.0
        w = (m21 - m12) / s if s else 1.0
        x = 0.25 * s
        y = (m01 + m10) / s if s else 0.0
        z = (m02 + m20) / s if s else 0.0
    elif m11 > m22:
        s = math.sqrt(max(0.0, 1.0 + m11 - m00 - m22)) * 2.0
        w = (m02 - m20) / s if s else 1.0
        x = (m01 + m10) / s if s else 0.0
        y = 0.25 * s
        z = (m12 + m21) / s if s else 0.0
    else:
        s = math.sqrt(max(0.0, 1.0 + m22 - m00 - m11)) * 2.0
        w = (m10 - m01) / s if s else 1.0
        x = (m02 + m20) / s if s else 0.0
        y = (m12 + m21) / s if s else 0.0
        z = 0.25 * s

    norm = math.sqrt(max(1e-16, (x * x) + (y * y) + (z * z) + (w * w)))
    return (x / norm, y / norm, z / norm, w / norm)


def _decompose_local_transform(matrix16: Tuple[float, ...]) -> JointTransform:
    # Assume row-major 4x4 with translation in last column.
    m00, m01, m02, m03 = matrix16[0], matrix16[1], matrix16[2], matrix16[3]
    m10, m11, m12, m13 = matrix16[4], matrix16[5], matrix16[6], matrix16[7]
    m20, m21, m22, m23 = matrix16[8], matrix16[9], matrix16[10], matrix16[11]

    sx = math.sqrt((m00 * m00) + (m10 * m10) + (m20 * m20))
    sy = math.sqrt((m01 * m01) + (m11 * m11) + (m21 * m21))
    sz = math.sqrt((m02 * m02) + (m12 * m12) + (m22 * m22))
    if sx <= 1e-12:
        sx = 1.0
    if sy <= 1e-12:
        sy = 1.0
    if sz <= 1e-12:
        sz = 1.0

    r00, r01, r02 = m00 / sx, m01 / sy, m02 / sz
    r10, r11, r12 = m10 / sx, m11 / sy, m12 / sz
    r20, r21, r22 = m20 / sx, m21 / sy, m22 / sz
    qx, qy, qz, qw = _quat_from_rotation_matrix(r00, r01, r02, r10, r11, r12, r20, r21, r22)

    xf = JointTransform(
        translation=(m03, m13, m23),
        rotation=(qx, qy, qz, qw),
        scale=(sx, sy, sz),
    )
    xf.validate()
    return xf


def _node_children_sorted(node_obj: Any) -> List[Any]:
    children = list(getattr(node_obj, "children", None) or [])
    indexed = list(enumerate(children))
    indexed.sort(
        key=lambda item: (
            _safe_text(getattr(item[1], "name", ""), f"child_{item[0]}").lower(),
            item[0],
        )
    )
    return [child for _, child in indexed]


def _build_node_records(root_node: Any) -> List[_NodeRecord]:
    records: List[_NodeRecord] = []

    def _visit(node_obj: Any, parent_idx: int | None) -> None:
        idx = len(records)
        rec = _NodeRecord(
            idx=idx,
            name=_safe_text(getattr(node_obj, "name", ""), f"joint_{idx}"),
            parent_idx=parent_idx,
            transform=_matrix4_from_obj(getattr(node_obj, "transformation", None)),
            node_obj=node_obj,
        )
        records.append(rec)
        for child in _node_children_sorted(node_obj):
            _visit(child, idx)

    _visit(root_node, None)
    return records


def _sorted_meshes(scene_obj: Any) -> List[Tuple[int, Any]]:
    meshes = list(getattr(scene_obj, "meshes", None) or [])
    indexed = list(enumerate(meshes))
    indexed.sort(
        key=lambda item: (
            _safe_text(getattr(item[1], "name", ""), f"mesh_{item[0]}").lower(),
            item[0],
        )
    )
    return indexed


def _sorted_bones(mesh_obj: Any) -> List[Tuple[int, Any]]:
    bones = list(getattr(mesh_obj, "bones", None) or [])
    indexed = list(enumerate(bones))
    indexed.sort(
        key=lambda item: (
            _safe_text(getattr(item[1], "name", ""), f"bone_{item[0]}").lower(),
            item[0],
        )
    )
    return indexed


def _weight_vertex_index(weight_obj: Any) -> int:
    for key in ("vertexid", "vertex_id", "mVertexId", "vertexId"):
        if hasattr(weight_obj, key):
            return _to_int(getattr(weight_obj, key), -1)
        if isinstance(weight_obj, dict) and key in weight_obj:
            return _to_int(weight_obj[key], -1)
    if isinstance(weight_obj, Sequence) and not isinstance(weight_obj, (str, bytes)):
        if len(weight_obj) >= 1:
            return _to_int(weight_obj[0], -1)
    return -1


def _weight_value(weight_obj: Any) -> float:
    for key in ("weight", "mWeight", "value"):
        if hasattr(weight_obj, key):
            return _to_float(getattr(weight_obj, key), 0.0)
        if isinstance(weight_obj, dict) and key in weight_obj:
            return _to_float(weight_obj[key], 0.0)
    if isinstance(weight_obj, Sequence) and not isinstance(weight_obj, (str, bytes)):
        if len(weight_obj) >= 2:
            return _to_float(weight_obj[1], 0.0)
    return 0.0


def _mesh_vertex_count(mesh_obj: Any) -> int:
    verts = getattr(mesh_obj, "vertices", None)
    if verts is None:
        return 0
    try:
        return int(len(verts))
    except Exception:
        pass
    return 0


def _mesh_vertices(mesh_obj: Any) -> List[Tuple[float, float, float]]:
    raw = getattr(mesh_obj, "vertices", None)
    if raw is None:
        return []
    tolist = getattr(raw, "tolist", None)
    if callable(tolist):
        try:
            raw = tolist()
        except Exception:
            pass
    try:
        seq = list(raw) if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else []
    except Exception:
        seq = []
    out: List[Tuple[float, float, float]] = []
    for value in seq:
        out.append(_vec3_from_obj(value))
    return out


def _mesh_faces(mesh_obj: Any) -> List[List[int]]:
    raw = getattr(mesh_obj, "faces", None)
    if raw is None:
        return []
    tolist = getattr(raw, "tolist", None)
    if callable(tolist):
        try:
            raw = tolist()
        except Exception:
            pass

    faces: List[List[int]] = []
    seq = list(raw) if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else []
    for entry in seq:
        conv = entry
        tolist = getattr(conv, "tolist", None)
        if callable(tolist):
            try:
                conv = tolist()
            except Exception:
                pass
        if isinstance(conv, Sequence) and not isinstance(conv, (str, bytes)):
            face = [_to_int(v, -1) for v in conv]
        else:
            face = []
        face = [idx for idx in face if idx >= 0]
        if len(face) >= 3:
            faces.append(face)
    return faces


def _triangulate_faces(faces: Sequence[Sequence[int]]) -> List[int]:
    tris: List[int] = []
    for face in faces:
        if len(face) < 3:
            continue
        if len(face) == 3:
            tris.extend([int(face[0]), int(face[1]), int(face[2])])
            continue
        f0 = int(face[0])
        for i in range(1, len(face) - 1):
            tris.extend([f0, int(face[i]), int(face[i + 1])])
    return tris


def _select_skeleton_nodes(
    node_records: Sequence[_NodeRecord],
    bone_names: Sequence[str],
    warnings: List[str],
) -> Tuple[List[int], Dict[str, int]]:
    if not bone_names:
        # Joints-only FBX (or meshes with no skin clusters): keep full hierarchy as skeleton.
        warnings.append(
            "No mesh bones were found; using node hierarchy as skeleton source."
        )
        return [rec.idx for rec in node_records], {}

    name_to_indices: Dict[str, List[int]] = {}
    for rec in node_records:
        name_to_indices.setdefault(rec.name, []).append(rec.idx)

    selected: set[int] = set()
    missing_bones: List[str] = []
    for bone_name in sorted(set(bone_names)):
        idxs = name_to_indices.get(bone_name, [])
        if not idxs:
            missing_bones.append(bone_name)
            continue
        chosen = idxs[0]
        if len(idxs) > 1:
            warnings.append(
                f"Bone '{bone_name}' matched multiple nodes; using first deterministically."
            )
        cur = chosen
        while cur is not None and cur not in selected:
            selected.add(cur)
            parent_idx = node_records[cur].parent_idx
            cur = parent_idx

    ordered = [rec.idx for rec in node_records if rec.idx in selected]
    synthetic_map: Dict[str, int] = {}
    for name in sorted(missing_bones):
        synthetic_map[name] = -1
    return ordered, synthetic_map


def _build_skeleton_asset(
    path_obj: Path,
    skeleton_name: str,
    node_records: Sequence[_NodeRecord],
    bone_offsets: Dict[str, Tuple[float, ...]],
    bone_names: Sequence[str],
    warnings: List[str],
) -> Tuple[SkeletonAsset, Dict[str, int]]:
    ordered_node_indices, synthetic_missing = _select_skeleton_nodes(
        node_records=node_records,
        bone_names=bone_names,
        warnings=warnings,
    )

    used_names: set[str] = set()
    node_to_joint_idx: Dict[int, int] = {}
    source_name_to_joint_idx: Dict[str, int] = {}
    joints: List[Joint] = []

    for node_idx in ordered_node_indices:
        rec = node_records[node_idx]
        joint_name = _unique_name(rec.name, used_names)
        parent_joint_idx = -1
        if rec.parent_idx is not None and rec.parent_idx in node_to_joint_idx:
            parent_joint_idx = node_to_joint_idx[rec.parent_idx]
        joint = Joint(
            name=joint_name,
            parent_index=parent_joint_idx,
            local_bind=_decompose_local_transform(rec.transform),
            inverse_bind_matrix=bone_offsets.get(rec.name, _IDENTITY_MATRIX_4X4),
        )
        joints.append(joint)
        jidx = len(joints) - 1
        node_to_joint_idx[node_idx] = jidx
        if rec.name not in source_name_to_joint_idx:
            source_name_to_joint_idx[rec.name] = jidx

    for missing_name in sorted(synthetic_missing.keys()):
        synthetic_name = _unique_name(missing_name, used_names)
        joint = Joint(
            name=synthetic_name,
            parent_index=-1,
            local_bind=JointTransform(),
            inverse_bind_matrix=bone_offsets.get(missing_name, _IDENTITY_MATRIX_4X4),
        )
        joints.append(joint)
        jidx = len(joints) - 1
        if missing_name not in source_name_to_joint_idx:
            source_name_to_joint_idx[missing_name] = jidx
        warnings.append(
            f"Bone '{missing_name}' was not found in node hierarchy; synthetic root joint created."
        )

    if not joints:
        # fallback for empty-bone scenes
        joints.append(
            Joint(
                name="root",
                parent_index=-1,
                local_bind=JointTransform(),
                inverse_bind_matrix=_IDENTITY_MATRIX_4X4,
            )
        )
        source_name_to_joint_idx["root"] = 0
        warnings.append("No bones were found; created synthetic root joint.")

    skeleton = SkeletonAsset(
        name=skeleton_name,
        joints=joints,
        metadata={
            "source_path": str(path_obj),
            "stage": "stage3_bind_ingest",
        },
    )
    skeleton.validate()
    return skeleton, source_name_to_joint_idx


def _build_mesh_assets(
    scene_obj: Any,
    skeleton: SkeletonAsset,
    bone_name_to_joint_idx: Dict[str, int],
    *,
    max_influences: int,
    warnings: List[str],
) -> List[SkeletalMeshAsset]:
    out: List[SkeletalMeshAsset] = []
    used_names: set[str] = set()

    for mesh_idx, mesh in _sorted_meshes(scene_obj):
        vertex_count = _mesh_vertex_count(mesh)
        if vertex_count <= 0:
            continue

        raw_mesh_name = _safe_text(getattr(mesh, "name", ""), f"mesh_{mesh_idx}")
        mesh_name = _unique_name(raw_mesh_name, used_names)

        faces = _mesh_faces(mesh)
        triangle_indices = _triangulate_faces(faces)
        triangle_indices = [idx for idx in triangle_indices if 0 <= idx < vertex_count]

        per_vertex: Dict[int, Dict[int, float]] = {}
        clamp_warning_count = 0
        clamp_warning_examples: List[str] = []
        max_warning_examples = 3
        for bone_order, bone in _sorted_bones(mesh):
            bone_name = _safe_text(getattr(bone, "name", ""), f"bone_{bone_order}")
            joint_idx = bone_name_to_joint_idx.get(bone_name)
            if joint_idx is None:
                warnings.append(
                    f"Mesh '{mesh_name}' bone '{bone_name}' does not map to skeleton; weights ignored."
                )
                continue

            weights = list(getattr(bone, "weights", None) or [])
            weights.sort(key=lambda w: (_weight_vertex_index(w), _weight_value(w)))
            for w in weights:
                vid = _weight_vertex_index(w)
                wt = _weight_value(w)
                if vid < 0 or vid >= vertex_count or wt <= 0.0:
                    continue
                slot = per_vertex.setdefault(vid, {})
                slot[joint_idx] = float(slot.get(joint_idx, 0.0)) + float(wt)

        vertex_skins: List[VertexSkin] = []
        for vid in sorted(per_vertex.keys()):
            inf_map = per_vertex[vid]
            packed = [(j, w) for j, w in inf_map.items() if w > 0.0]
            packed.sort(key=lambda it: (-float(it[1]), int(it[0])))
            if max_influences > 0 and len(packed) > max_influences:
                clamp_warning_count += 1
                if len(clamp_warning_examples) < max_warning_examples:
                    clamp_warning_examples.append(
                        f"Mesh '{mesh_name}' vertex {vid} had {len(packed)} influences; clamped to {max_influences}."
                    )
                packed = packed[:max_influences]
            total = sum(float(w) for _, w in packed)
            if total <= 0.0:
                continue
            influences = [
                VertexInfluence(joint_index=int(j), weight=float(w) / float(total))
                for j, w in packed
            ]
            vertex_skins.append(VertexSkin(vertex_index=int(vid), influences=influences))

        if clamp_warning_examples:
            warnings.extend(clamp_warning_examples)
        if clamp_warning_count > len(clamp_warning_examples):
            warnings.append(
                f"Mesh '{mesh_name}' had {clamp_warning_count - len(clamp_warning_examples)} additional "
                f"vertices with >{max_influences} influences (warning list truncated)."
            )

        bind_positions = _mesh_vertices(mesh)
        if bind_positions and len(bind_positions) != vertex_count:
            if len(bind_positions) > vertex_count:
                bind_positions = bind_positions[:vertex_count]
            else:
                pad_count = vertex_count - len(bind_positions)
                bind_positions.extend([(0.0, 0.0, 0.0)] * int(pad_count))

        asset = SkeletalMeshAsset(
            name=mesh_name,
            skeleton_name=skeleton.name,
            vertex_count=vertex_count,
            triangle_indices=triangle_indices,
            vertex_skins=vertex_skins,
            metadata={
                "source_mesh_index": int(mesh_idx),
                "source_mesh_name": raw_mesh_name,
                "bind_positions": [list(v) for v in bind_positions],
            },
        )
        asset.validate(skeleton)
        out.append(asset)

    return out


def _vec3_from_obj(value: Any) -> Tuple[float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0)
    try:
        return (
            _to_float(value[0], 0.0),  # type: ignore[index]
            _to_float(value[1], 0.0),  # type: ignore[index]
            _to_float(value[2], 0.0),  # type: ignore[index]
        )
    except Exception:
        pass
    for keys in (("x", "y", "z"), ("mData",)):
        try:
            if keys == ("mData",):
                data = getattr(value, "mData")
                return (
                    _to_float(data[0], 0.0),
                    _to_float(data[1], 0.0),
                    _to_float(data[2], 0.0),
                )
            if all(hasattr(value, k) for k in keys):
                return (
                    _to_float(getattr(value, "x"), 0.0),
                    _to_float(getattr(value, "y"), 0.0),
                    _to_float(getattr(value, "z"), 0.0),
                )
        except Exception:
            pass
    return (0.0, 0.0, 0.0)


def _matrix4_mul_row_major(a: Tuple[float, ...], b: Tuple[float, ...]) -> Tuple[float, ...]:
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[(r * 4) + c] = (
                (a[(r * 4) + 0] * b[(0 * 4) + c])
                + (a[(r * 4) + 1] * b[(1 * 4) + c])
                + (a[(r * 4) + 2] * b[(2 * 4) + c])
                + (a[(r * 4) + 3] * b[(3 * 4) + c])
            )
    return tuple(out)


def _fbxsdk_matrix4_tuple(mat_obj: Any) -> Tuple[float, ...]:
    if mat_obj is None:
        return _IDENTITY_MATRIX_4X4
    try:
        return tuple(_to_float(mat_obj.Get(r, c), 0.0) for r in range(4) for c in range(4))
    except Exception:
        pass
    try:
        return tuple(_to_float(mat_obj[r][c], 0.0) for r in range(4) for c in range(4))  # type: ignore[index]
    except Exception:
        pass
    return _matrix4_from_obj(mat_obj)


def _fbxsdk_node_local_matrix_tuple(node_obj: Any) -> Tuple[float, ...]:
    if node_obj is None:
        return _IDENTITY_MATRIX_4X4
    try:
        mat = node_obj.EvaluateLocalTransform()
        return _fbxsdk_matrix4_tuple(mat)
    except Exception:
        pass
    return _IDENTITY_MATRIX_4X4


def _fbxsdk_cluster_inverse_bind(fbx_mod: Any, cluster_obj: Any) -> Tuple[float, ...]:
    if cluster_obj is None:
        return _IDENTITY_MATRIX_4X4
    try:
        transform = fbx_mod.FbxAMatrix()
        link = fbx_mod.FbxAMatrix()
        cluster_obj.GetTransformMatrix(transform)
        cluster_obj.GetTransformLinkMatrix(link)
    except Exception:
        return _IDENTITY_MATRIX_4X4

    try:
        return _fbxsdk_matrix4_tuple(link.Inverse() * transform)
    except Exception:
        pass

    try:
        inv_link = _fbxsdk_matrix4_tuple(link.Inverse())
        geom = _fbxsdk_matrix4_tuple(transform)
        return _matrix4_mul_row_major(inv_link, geom)
    except Exception:
        return _IDENTITY_MATRIX_4X4


def _fbxsdk_skin_deformer_type(fbx_mod: Any) -> Any | None:
    deformer_cls = getattr(fbx_mod, "FbxDeformer", None)
    if deformer_cls is None:
        return None
    direct = getattr(deformer_cls, "eSkin", None)
    if direct is not None:
        return direct
    enum_cls = getattr(deformer_cls, "EDeformerType", None)
    if enum_cls is None:
        return None
    return getattr(enum_cls, "eSkin", None)


def _build_simple_node_from_fbxsdk(node_obj: Any) -> _SimpleNode:
    name = _safe_text(getattr(node_obj, "GetName", lambda: "")(), "node")
    out = _SimpleNode(
        name=name,
        transformation=_fbxsdk_node_local_matrix_tuple(node_obj),
    )
    child_count = _to_int(getattr(node_obj, "GetChildCount", lambda: 0)(), 0)
    for i in range(max(0, child_count)):
        try:
            child = node_obj.GetChild(i)
        except Exception:
            child = None
        if child is None:
            continue
        out.children.append(_build_simple_node_from_fbxsdk(child))
    return out


def _build_simple_mesh_from_fbxsdk(
    fbx_mod: Any,
    node_obj: Any,
    mesh_obj: Any,
    mesh_index: int,
) -> _SimpleMesh:
    mesh_name = _safe_text(
        getattr(mesh_obj, "GetName", lambda: "")(),
        _safe_text(getattr(node_obj, "GetName", lambda: "")(), f"mesh_{mesh_index}"),
    )
    out = _SimpleMesh(name=mesh_name)

    # control points
    cp_count = _to_int(getattr(mesh_obj, "GetControlPointsCount", lambda: 0)(), 0)
    cps = None
    try:
        cps = mesh_obj.GetControlPoints()
    except Exception:
        cps = None
    if cp_count > 0 and cps is not None:
        for i in range(cp_count):
            try:
                out.vertices.append(_vec3_from_obj(cps[i]))
            except Exception:
                out.vertices.append((0.0, 0.0, 0.0))

    # polygons
    poly_count = _to_int(getattr(mesh_obj, "GetPolygonCount", lambda: 0)(), 0)
    for pidx in range(max(0, poly_count)):
        psize = _to_int(getattr(mesh_obj, "GetPolygonSize", lambda _i: 0)(pidx), 0)
        face: List[int] = []
        for corner in range(max(0, psize)):
            vid = _to_int(
                getattr(mesh_obj, "GetPolygonVertex", lambda _p, _c: -1)(pidx, corner),
                -1,
            )
            if vid >= 0:
                face.append(vid)
        if len(face) >= 3:
            out.faces.append(face)

    # skin clusters
    bones_by_name: Dict[str, _SimpleBone] = {}
    skin_type = _fbxsdk_skin_deformer_type(fbx_mod)
    skin_count = _to_int(
        getattr(mesh_obj, "GetDeformerCount", lambda *_args: 0)(skin_type)
        if skin_type is not None
        else 0,
        0,
    )
    for skin_index in range(max(0, skin_count)):
        try:
            skin = mesh_obj.GetDeformer(skin_index, skin_type)
        except Exception:
            skin = None
        if skin is None:
            continue
        cluster_count = _to_int(getattr(skin, "GetClusterCount", lambda: 0)(), 0)
        for cluster_index in range(max(0, cluster_count)):
            try:
                cluster = skin.GetCluster(cluster_index)
            except Exception:
                cluster = None
            if cluster is None:
                continue
            link = None
            try:
                link = cluster.GetLink()
            except Exception:
                link = None
            bone_name = _safe_text(
                getattr(link, "GetName", lambda: "")() if link is not None else "",
                f"bone_{cluster_index}",
            )
            indices_count = _to_int(
                getattr(cluster, "GetControlPointIndicesCount", lambda: 0)(),
                0,
            )
            try:
                cp_indices = cluster.GetControlPointIndices()
            except Exception:
                cp_indices = []
            try:
                cp_weights = cluster.GetControlPointWeights()
            except Exception:
                cp_weights = []

            weights: List[_SimpleWeight] = []
            for wi in range(max(0, indices_count)):
                try:
                    vid = _to_int(cp_indices[wi], -1)
                except Exception:
                    vid = -1
                try:
                    wt = _to_float(cp_weights[wi], 0.0)
                except Exception:
                    wt = 0.0
                if vid >= 0 and wt > 0.0:
                    weights.append(_SimpleWeight(vertexid=vid, weight=wt))

            offset = _fbxsdk_cluster_inverse_bind(fbx_mod, cluster)
            prev = bones_by_name.get(bone_name)
            if prev is None:
                bones_by_name[bone_name] = _SimpleBone(
                    name=bone_name,
                    weights=weights,
                    offsetmatrix=offset,
                )
            else:
                prev.weights.extend(weights)

    out.bones = sorted(bones_by_name.values(), key=lambda b: b.name.lower())
    return out


def _build_simple_scene_from_fbxsdk(fbx_mod: Any, scene_obj: Any) -> _SimpleScene:
    try:
        root_native = scene_obj.GetRootNode()
    except Exception as exc:
        raise FBXBindIngestError(f"FBX SDK scene has no root node: {exc}") from exc
    if root_native is None:
        raise FBXBindIngestError("FBX SDK scene has no root node.")

    root = _build_simple_node_from_fbxsdk(root_native)
    meshes: List[_SimpleMesh] = []

    def _visit(node_obj: Any) -> None:
        if node_obj is None:
            return
        mesh = None
        try:
            mesh = node_obj.GetMesh()
        except Exception:
            mesh = None
        if mesh is not None:
            meshes.append(
                _build_simple_mesh_from_fbxsdk(
                    fbx_mod=fbx_mod,
                    node_obj=node_obj,
                    mesh_obj=mesh,
                    mesh_index=len(meshes),
                )
            )
        child_count = _to_int(getattr(node_obj, "GetChildCount", lambda: 0)(), 0)
        for i in range(max(0, child_count)):
            try:
                _visit(node_obj.GetChild(i))
            except Exception:
                continue

    _visit(root_native)
    return _SimpleScene(rootnode=root, meshes=meshes)


def _fbxsdk_status_error_text(status_owner: Any) -> str:
    try:
        status = status_owner.GetStatus()
        text = status.GetErrorString() if status is not None else ""
        text = str(text or "").strip()
        if text:
            return text
    except Exception:
        pass
    return "unknown FBX SDK error"


@contextmanager
def _load_fbxsdk_scene(path_obj: Path) -> Iterator[Any]:
    try:
        import fbx  # type: ignore
    except Exception as exc:
        raise FBXBindIngestError(f"fbx sdk unavailable: {exc}") from exc

    manager = None
    importer = None
    try:
        manager = fbx.FbxManager.Create()
        if manager is None:
            raise FBXBindIngestError("fbx sdk manager creation failed.")
        ios = fbx.FbxIOSettings.Create(manager, getattr(fbx, "IOSROOT", ""))
        if ios is not None:
            manager.SetIOSettings(ios)

        importer = fbx.FbxImporter.Create(manager, "")
        if importer is None:
            raise FBXBindIngestError("fbx sdk importer creation failed.")
        try:
            ok = importer.Initialize(str(path_obj), -1, manager.GetIOSettings())
        except Exception:
            ok = importer.Initialize(str(path_obj), -1)
        if not ok:
            raise FBXBindIngestError(
                f"Failed to initialize FBX SDK importer: {_fbxsdk_status_error_text(importer)}"
            )

        scene = fbx.FbxScene.Create(manager, path_obj.stem or "scene")
        if scene is None:
            raise FBXBindIngestError("fbx sdk scene creation failed.")
        if not importer.Import(scene):
            raise FBXBindIngestError(
                f"Failed to import FBX with FBX SDK: {_fbxsdk_status_error_text(importer)}"
            )

        yield _build_simple_scene_from_fbxsdk(fbx, scene)
    except FBXBindIngestError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise FBXBindIngestError(f"Failed to load FBX via fbx sdk: {exc}") from exc
    finally:
        try:
            if importer is not None:
                importer.Destroy()
        except Exception:
            pass
        try:
            if manager is not None:
                manager.Destroy()
        except Exception:
            pass


def _try_ensure_assimp_runtime() -> None:
    try:
        from echograph.ui.gl_loaders import ensure_assimp_dll  # lazy import
    except Exception:
        return
    try:
        ensure_assimp_dll()
    except Exception:
        pass


@contextmanager
def _load_pyassimp_scene(path_obj: Path) -> Iterator[Any]:
    _try_ensure_assimp_runtime()
    try:
        import pyassimp  # type: ignore
        from pyassimp import postprocess as ai_post  # type: ignore
    except Exception as exc:
        raise FBXBindIngestError(f"pyassimp unavailable: {exc}") from exc

    processing = 0
    for name in (
        "aiProcess_JoinIdenticalVertices",
        "aiProcess_SortByPType",
        "aiProcess_FindInvalidData",
        "aiProcess_ImproveCacheLocality",
        "aiProcess_OptimizeMeshes",
    ):
        processing |= int(getattr(ai_post, name, 0) or 0)

    attempts: List[Tuple[str, Dict[str, Any]]] = [
        ("fbx+processing", {"file_type": "fbx", "processing": processing}),
        ("fbx+no_processing", {"file_type": "fbx", "processing": 0}),
        ("auto+no_processing", {"processing": 0}),
    ]
    failures: List[str] = []
    for label, kwargs in attempts:
        try:
            with pyassimp.load(str(path_obj), **kwargs) as scene:
                yield scene
                return
        except Exception as exc:
            failures.append(f"{label}: {exc}")

    summary = " | ".join(failures[:3])
    raise FBXBindIngestError(f"Failed to load FBX via pyassimp: {summary}")


def _ingest_scene(
    scene_obj: Any,
    *,
    path_obj: Path,
    skeleton_name: str | None,
    max_influences: int,
) -> FBXBindIngestResult:
    warnings: List[str] = []
    root = getattr(scene_obj, "rootnode", None)
    if root is None:
        raise FBXBindIngestError("FBX scene is missing rootnode.")

    node_records = _build_node_records(root)

    bone_offsets: Dict[str, Tuple[float, ...]] = {}
    bone_names: List[str] = []
    for mesh_idx, mesh in _sorted_meshes(scene_obj):
        for bone_idx, bone in _sorted_bones(mesh):
            bone_name = _safe_text(getattr(bone, "name", ""), f"bone_{mesh_idx}_{bone_idx}")
            bone_names.append(bone_name)
            off = _matrix4_from_obj(getattr(bone, "offsetmatrix", None))
            prev = bone_offsets.get(bone_name)
            if prev is None:
                bone_offsets[bone_name] = off
            elif prev != off:
                warnings.append(
                    f"Bone '{bone_name}' had inconsistent inverse bind matrices; first value kept."
                )

    skeleton_asset_name = _safe_text(
        skeleton_name,
        f"{path_obj.stem}_Skeleton",
    )
    skeleton, source_name_to_joint_idx = _build_skeleton_asset(
        path_obj=path_obj,
        skeleton_name=skeleton_asset_name,
        node_records=node_records,
        bone_offsets=bone_offsets,
        bone_names=bone_names,
        warnings=warnings,
    )
    mesh_assets = _build_mesh_assets(
        scene_obj=scene_obj,
        skeleton=skeleton,
        bone_name_to_joint_idx=source_name_to_joint_idx,
        max_influences=max(1, int(max_influences)),
        warnings=warnings,
    )

    return FBXBindIngestResult(
        source_path=str(path_obj),
        skeleton=skeleton,
        meshes=mesh_assets,
        warnings=warnings,
    )


def _path_cache_token(path_obj: Path) -> Tuple[str, int, int]:
    try:
        resolved = str(path_obj.resolve())
    except Exception:
        resolved = str(path_obj)
    if os.name == "nt":
        resolved = resolved.lower()
    stat = path_obj.stat()
    return (resolved, int(stat.st_mtime_ns), int(stat.st_size))


def _bind_cache_key(
    path_obj: Path,
    *,
    skeleton_name: str | None,
    max_influences: int,
) -> Tuple[str, int, int, str, int]:
    path_token = _path_cache_token(path_obj)
    return (
        path_token[0],
        path_token[1],
        path_token[2],
        str(skeleton_name or "").strip(),
        int(max_influences),
    )


def _bind_cache_get(key: Tuple[str, int, int, str, int]) -> FBXBindIngestResult | None:
    cached = _BIND_INGEST_CACHE.get(key)
    if cached is None:
        return None
    _BIND_INGEST_CACHE.move_to_end(key)
    return cached


def _bind_cache_put(
    key: Tuple[str, int, int, str, int],
    value: FBXBindIngestResult,
) -> None:
    _BIND_INGEST_CACHE[key] = value
    _BIND_INGEST_CACHE.move_to_end(key)
    while len(_BIND_INGEST_CACHE) > int(_BIND_INGEST_CACHE_MAX):
        _BIND_INGEST_CACHE.popitem(last=False)


def ingest_fbx_bind_data(
    path: str | Path,
    *,
    skeleton_name: str | None = None,
    max_influences: int = 64,
    scene: Any | None = None,
) -> FBXBindIngestResult:
    path_obj = Path(path)
    influence_limit = max(1, int(max_influences))
    if scene is not None:
        return _ingest_scene(
            scene_obj=scene,
            path_obj=path_obj,
            skeleton_name=skeleton_name,
            max_influences=influence_limit,
        )

    if not path_obj.exists():
        raise FBXBindIngestError(f"FBX path does not exist: {path_obj}")
    cache_key = _bind_cache_key(
        path_obj,
        skeleton_name=skeleton_name,
        max_influences=influence_limit,
    )
    cached = _bind_cache_get(cache_key)
    if cached is not None:
        return cached

    failures: List[str] = []
    for backend_name, loader in (
        ("fbx sdk", _load_fbxsdk_scene),
        ("pyassimp", _load_pyassimp_scene),
    ):
        try:
            with loader(path_obj) as scene_obj:
                result = _ingest_scene(
                    scene_obj=scene_obj,
                    path_obj=path_obj,
                    skeleton_name=skeleton_name,
                    max_influences=influence_limit,
                )
                _bind_cache_put(cache_key, result)
                return result
        except FBXBindIngestError as exc:
            failures.append(f"{backend_name}: {exc}")

    raise FBXBindIngestError(
        "Failed to load FBX with available backends: " + " | ".join(failures)
    )


def _parent_name_map(skeleton: SkeletonAsset) -> Dict[str, str | None]:
    mapping: Dict[str, str | None] = {}
    for joint in skeleton.joints:
        parent_name = None
        if joint.parent_index >= 0:
            parent_name = skeleton.joints[joint.parent_index].name
        mapping[joint.name] = parent_name
    return mapping


def compare_skeleton_layout(
    base: SkeletonAsset,
    override: SkeletonAsset,
) -> SkeletonCompatibilityReport:
    base.validate()
    override.validate()

    errors: List[str] = []
    warnings: List[str] = []
    base_map = _parent_name_map(base)
    override_map = _parent_name_map(override)

    for joint_name, parent_name in base_map.items():
        if joint_name not in override_map:
            errors.append(f"Missing required joint '{joint_name}'.")
            continue
        other_parent = override_map[joint_name]
        if parent_name != other_parent:
            errors.append(
                f"Joint '{joint_name}' parent mismatch: expected {parent_name!r}, got {other_parent!r}."
            )

    extras = sorted(set(override_map.keys()) - set(base_map.keys()))
    if extras:
        warnings.append(
            "Override contains extra joints not present in base skeleton: "
            + ", ".join(extras[:12])
            + (" ..." if len(extras) > 12 else "")
        )

    return SkeletonCompatibilityReport(
        compatible=not errors,
        errors=errors,
        warnings=warnings,
    )


__all__ = [
    "FBXBindIngestError",
    "FBXBindIngestResult",
    "SkeletonCompatibilityReport",
    "ingest_fbx_bind_data",
    "compare_skeleton_layout",
]
