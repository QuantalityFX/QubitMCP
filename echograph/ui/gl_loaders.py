# echograph/ui/gl_loaders.py
from __future__ import annotations

import os
import ctypes
import io
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from .gl_types import ModelData, MeshArrays, SubMeshData
import base64
import struct
import json
import math

try:
    import numpy as np
except Exception:
    np = None

try:
    from PIL import Image as PILImage
except Exception:
    PILImage = None

_MODEL_LOADERS: Dict[str, Callable[[Path], "ModelData"]] = {}

_COMPONENT_SIZES = {
    5120: 1,  # BYTE
    5121: 1,  # UNSIGNED_BYTE
    5122: 2,  # SHORT
    5123: 2,  # UNSIGNED_SHORT
    5125: 4,  # UNSIGNED_INT
    5126: 4,  # FLOAT
}

_COMPONENT_FORMATS = {
    5120: "b",
    5121: "B",
    5122: "h",
    5123: "H",
    5125: "I",
    5126: "f",
}

_TYPE_COUNTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}

def register_model_loader(exts: Iterable[str], loader: Callable[[Path], "ModelData"]) -> None:
    for ext in exts:
        key = (ext or "").strip().lower()
        if not key:
            continue
        if not key.startswith("."):
            key = f".{key}"
        _MODEL_LOADERS[key] = loader

_ASSIMP_DLL_READY = False

def ensure_assimp_dll() -> None:
    global _ASSIMP_DLL_READY
    if _ASSIMP_DLL_READY:
        return
    if os.name != "nt":
        _ASSIMP_DLL_READY = True
        return
    candidates: List[Path] = []
    try:
        root = Path(__file__).resolve().parents[2]
        vcpkg_bin = root / "vcpkg" / "installed" / "x64-windows" / "bin"
        if vcpkg_bin.exists():
            candidates.append(vcpkg_bin)
    except Exception:
        pass
    env_path = os.environ.get("ASSIMP_LIBRARY_PATH") or os.environ.get("ASSIMP_LIBRARY")
    if env_path:
        try:
            env_candidate = Path(env_path)
            if env_candidate.is_file():
                env_candidate = env_candidate.parent
            candidates.append(env_candidate)
        except Exception:
            pass
    for candidate in candidates:
        try:
            if candidate and candidate.exists():
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(str(candidate))
                os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")
                if "ASSIMP_LIBRARY" not in os.environ:
                    dlls = list(candidate.glob("assimp*.dll"))
                    if dlls:
                        os.environ["ASSIMP_LIBRARY"] = str(dlls[0])
                break
        except Exception:
            continue
    _ASSIMP_DLL_READY = True

def _normalize_color(value: object) -> Optional[tuple]:
    try:
        vals = [float(v) for v in value]
    except Exception:
        return None
    if not vals:
        return None
    if len(vals) == 3:
        vals.append(1.0)
    vals = vals[:4]
    if max(vals) > 1.0:
        vals = [v / 255.0 for v in vals]
    return tuple(max(0.0, min(1.0, v)) for v in vals)



def _pyassimp_texture_to_image(texture: object) -> Optional[object]:
    if PILImage is None or texture is None:
        return None
    try:
        width = int(texture.mWidth)
        height = int(texture.mHeight)
    except Exception:
        return None
    if width <= 0:
        return None
    try:
        data_ptr = ctypes.cast(texture.pcData, ctypes.POINTER(ctypes.c_ubyte))
    except Exception:
        return None
    if height == 0:
        try:
            raw = ctypes.string_at(data_ptr, width)
            return PILImage.open(io.BytesIO(raw)).convert("RGBA")
        except Exception:
            return None
    size = width * height * 4
    try:
        raw = ctypes.string_at(data_ptr, size)
        img = PILImage.frombytes("RGBA", (width, height), raw, "raw", "ARGB")
        return img
    except Exception:
        return None

def load_fbx_mesh_arrays_pyassimp(path: Path) -> MeshArrays:

    if np is None:
        raise RuntimeError("numpy unavailable")
    ensure_assimp_dll()
    try:
        import pyassimp
        from pyassimp import material as ai_material
    except Exception as exc:
        raise RuntimeError(f"pyassimp unavailable: {exc}")
    try:
        from pyassimp import postprocess as ai_post
        processing = (
            ai_post.aiProcess_Triangulate
            | ai_post.aiProcess_PreTransformVertices
            | ai_post.aiProcess_JoinIdenticalVertices
        )
        with pyassimp.load(str(path), file_type="fbx", processing=processing) as scene:
            points_all: List["np.ndarray"] = []
            normals_all: List["np.ndarray"] = []
            uvs_all: List["np.ndarray"] = []
            submeshes: List[SubMeshData] = []
            texture_path = None
            texture_image = None
            base_color = None
            for mesh in scene.meshes or []:
                vertices = np.asarray(getattr(mesh, "vertices", []), dtype="f4")
                faces = np.asarray(getattr(mesh, "faces", []), dtype=np.int64)
                if vertices.size == 0 or faces.size == 0:
                    continue
                tri_vertices = vertices[faces].reshape(-1, 3)
                normals = getattr(mesh, "normals", None)
                if normals is not None and len(normals) == len(vertices):
                    norm_arr = np.asarray(normals, dtype="f4")[faces].reshape(-1, 3)
                else:
                    v0 = vertices[faces[:, 0]]
                    v1 = vertices[faces[:, 1]]
                    v2 = vertices[faces[:, 2]]
                    n = np.cross(v1 - v0, v2 - v0)
                    lengths = np.linalg.norm(n, axis=1)
                    lengths[lengths < 1e-6] = 1.0
                    n = (n.T / lengths).T
                    norm_arr = np.repeat(n[:, None, :], 3, axis=1).reshape(-1, 3)

                uv = None
                texcoords = getattr(mesh, "texturecoords", None)
                if texcoords is not None and len(texcoords) > 0:
                    uv_raw = np.asarray(texcoords[0], dtype="f4")
                    if uv_raw.shape[0] == vertices.shape[0] and uv_raw.shape[1] >= 2:
                        uv = uv_raw[:, :2]
                if uv is None:
                    uv = np.zeros((vertices.shape[0], 2), dtype="f4")
                tri_uv = uv[faces].reshape(-1, 2)

                tri_vertices = tri_vertices.astype("f4")
                norm_arr = norm_arr.astype("f4")
                tri_uv = tri_uv.astype("f4")
                points_all.append(tri_vertices)
                normals_all.append(norm_arr)
                uvs_all.append(tri_uv)

                material = getattr(mesh, "material", None)
                if material is None and hasattr(scene, "materials"):
                    try:
                        material = scene.materials[mesh.materialindex]
                    except Exception:
                        material = None
                props = getattr(material, "properties", {}) if material is not None else {}
                mesh_texture_path = None
                mesh_texture_image = None
                mesh_color = None
                if props:
                    tex_value = None
                    for semantic in (
                        ai_material.aiTextureType_DIFFUSE,
                        ai_material.aiTextureType_UNKNOWN,
                        ai_material.aiTextureType_EMISSIVE,
                    ):
                        tex_value = props.get(("file", semantic))
                        if isinstance(tex_value, str) and tex_value:
                            break
                        tex_value = None
                    if tex_value is None:
                        for (key, _semantic), value in props.items():
                            if key == "file" and isinstance(value, str) and value:
                                tex_value = value
                                break
                    if isinstance(tex_value, str) and tex_value.startswith("*"):
                        try:
                            tex_index = int(tex_value[1:])
                            if 0 <= tex_index < len(scene.textures):
                                mesh_texture_image = _pyassimp_texture_to_image(scene.textures[tex_index])
                        except Exception:
                            mesh_texture_image = None
                    elif isinstance(tex_value, str) and tex_value:
                        candidate = Path(tex_value)
                        if not candidate.is_absolute():
                            candidate = (path.parent / candidate).resolve()
                        if candidate.exists():
                            mesh_texture_path = candidate

                if props:
                    color_val = props.get(("diffuse", ai_material.aiTextureType_NONE)) if props else None
                    if color_val is None:
                        color_val = props.get(("color", ai_material.aiTextureType_NONE)) if props else None
                    if color_val is not None:
                        mesh_color = _normalize_color(color_val)

                submeshes.append(
                    SubMeshData(
                        points=tri_vertices,
                        normals=norm_arr,
                        uvs=tri_uv,
                        texture_path=mesh_texture_path,
                        texture_image=mesh_texture_image,
                        base_color=mesh_color,
                    )
                )

                if texture_path is None and texture_image is None:
                    if mesh_texture_path is not None or mesh_texture_image is not None:
                        texture_path = mesh_texture_path
                        texture_image = mesh_texture_image

                if base_color is None and mesh_color is not None:
                    base_color = mesh_color

            if not points_all:
                raise RuntimeError("FBX mesh empty")
            points = np.concatenate(points_all, axis=0)
            normals = np.concatenate(normals_all, axis=0)
            uvs = np.concatenate(uvs_all, axis=0)
            return MeshArrays(
                points=points,
                normals=normals,
                uvs=uvs,
                texture_path=texture_path,
                texture_image=texture_image,
                base_color=base_color,
                submeshes=submeshes if submeshes else None,
            )
    except Exception as exc:
        raise RuntimeError(str(exc))


def _resolve_obj_index(value: Optional[int], total: int) -> Optional[int]:
    if value is None:
        return None
    if value < 0:
        value = total + value + 1
    if value <= 0 or value > total:
        return None
    return value - 1

def load_obj_mesh_arrays(path: Path) -> Tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    if np is None:
        raise RuntimeError("numpy unavailable")
    positions: List[Tuple[float, float, float]] = []
    texcoords: List[Tuple[float, float]] = []
    normals: List[Tuple[float, float, float]] = []
    out_pos: List[float] = []
    out_uv: List[float] = []
    out_norm: List[float] = []

    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        raw = path.read_text(errors="ignore")

    faces: List[List[Tuple[Optional[int], Optional[int], Optional[int]]]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts:
            continue
        head = parts[0].lower()
        if head == "v" and len(parts) >= 4:
            try:
                positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except Exception:
                continue
        elif head == "vt" and len(parts) >= 3:
            try:
                texcoords.append((float(parts[1]), float(parts[2])))
            except Exception:
                continue
        elif head == "vn" and len(parts) >= 4:
            try:
                normals.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except Exception:
                continue
        elif head == "f" and len(parts) >= 4:
            face: List[Tuple[Optional[int], Optional[int], Optional[int]]] = []
            for token in parts[1:]:
                if not token:
                    continue
                vals = token.split("/")
                v_idx = int(vals[0]) if vals[0] else None
                vt_idx = int(vals[1]) if len(vals) > 1 and vals[1] else None
                vn_idx = int(vals[2]) if len(vals) > 2 and vals[2] else None
                face.append((v_idx, vt_idx, vn_idx))
            if len(face) >= 3:
                faces.append(face)

    use_normals = bool(normals)
    for face in faces:
        root = face[0]
        for i in range(1, len(face) - 1):
            tri = (root, face[i], face[i + 1])
            tri_pos: List[Tuple[float, float, float]] = []
            tri_uv: List[Tuple[float, float]] = []
            tri_norm: List[Tuple[float, float, float]] = []
            for v_idx, vt_idx, vn_idx in tri:
                pos_idx = _resolve_obj_index(v_idx, len(positions))
                if pos_idx is None:
                    continue
                vx, vy, vz = positions[pos_idx]
                tri_pos.append((vx, vy, vz))
                uv_idx = _resolve_obj_index(vt_idx, len(texcoords))
                if uv_idx is not None:
                    u, v = texcoords[uv_idx]
                else:
                    u, v = 0.0, 0.0
                tri_uv.append((u, v))
                if use_normals:
                    n_idx = _resolve_obj_index(vn_idx, len(normals))
                    if n_idx is not None:
                        nx, ny, nz = normals[n_idx]
                    else:
                        nx, ny, nz = 0.0, 0.0, 0.0
                else:
                    nx, ny, nz = 0.0, 0.0, 0.0
                tri_norm.append((nx, ny, nz))
            if len(tri_pos) != 3:
                continue
            if not use_normals or all((nx == 0.0 and ny == 0.0 and nz == 0.0) for nx, ny, nz in tri_norm):
                ax, ay, az = tri_pos[0]
                bx, by, bz = tri_pos[1]
                cx, cy, cz = tri_pos[2]
                n = np.cross(np.array([bx - ax, by - ay, bz - az], dtype="f4"), np.array([cx - ax, cy - ay, cz - az], dtype="f4"))
                length = float(np.linalg.norm(n))
                if length > 1e-6:
                    n = n / length
                tri_norm = [(float(n[0]), float(n[1]), float(n[2]))] * 3
            for (vx, vy, vz), (u, v), (nx, ny, nz) in zip(tri_pos, tri_uv, tri_norm):
                out_pos.extend([vx, vy, vz])
                out_uv.extend([u, v])
                out_norm.extend([nx, ny, nz])

    pos_arr = np.array(out_pos, dtype="f4").reshape(-1, 3)
    norm_arr = np.array(out_norm, dtype="f4").reshape(-1, 3)
    uv_arr = np.array(out_uv, dtype="f4").reshape(-1, 2)
    return pos_arr, norm_arr, uv_arr

def _read_glb(path: Path) -> Tuple[dict, List[bytes]]:
    raw = path.read_bytes()
    if raw[:4] != b"glTF":
        raise ValueError("Not a GLB file.")
    if len(raw) < 12:
        raise ValueError("Invalid GLB header.")
    total_len = struct.unpack_from("<I", raw, 8)[0]
    if total_len > len(raw):
        total_len = len(raw)
    offset = 12
    gltf = None
    buffers: List[bytes] = []
    while offset + 8 <= total_len:
        chunk_len, chunk_type = struct.unpack_from("<II", raw, offset)
        offset += 8
        chunk_data = raw[offset: offset + chunk_len]
        offset += chunk_len
        if chunk_type == 0x4E4F534A:  # JSON
            gltf = json.loads(chunk_data.decode("utf-8"))
        elif chunk_type == 0x004E4942:  # BIN
            buffers.append(bytes(chunk_data))
    if gltf is None:
        raise ValueError("GLB missing JSON chunk.")
    return gltf, buffers


def _read_gltf(path: Path) -> Tuple[dict, List[bytes]]:
    gltf = json.loads(path.read_text(encoding="utf-8"))
    buffers: List[bytes] = []
    for buf in gltf.get("buffers", []) or []:
        uri = (buf.get("uri") or "").strip()
        if uri.startswith("data:"):
            buffers.append(_decode_data_uri(uri))
        else:
            buf_path = (path.parent / uri).resolve()
            buffers.append(buf_path.read_bytes())
    return gltf, buffers

def _read_accessor(gltf: dict, buffers: List[bytes], accessor_index: int) -> List[float]:
    accessor = gltf.get("accessors", [])[accessor_index]
    buffer_view = gltf.get("bufferViews", [])[accessor["bufferView"]]
    buffer_data = buffers[buffer_view["buffer"]]
    offset = int(buffer_view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    count = int(accessor.get("count", 0))
    ctype = int(accessor.get("componentType", 5126))
    fmt = _COMPONENT_FORMATS.get(ctype, "f")
    comp_size = _COMPONENT_SIZES.get(ctype, 4)
    ncomp = _TYPE_COUNTS.get(accessor.get("type", "SCALAR"), 1)
    stride = int(buffer_view.get("byteStride", comp_size * ncomp))
    out: List[float] = []
    for i in range(count):
        base = offset + i * stride
        for c in range(ncomp):
            val = struct.unpack_from("<" + fmt, buffer_data, base + c * comp_size)[0]
            out.append(float(val))
    return out


def _mul_mat4(a: List[float], b: List[float]) -> List[float]:
    out = [0.0] * 16
    for row in range(4):
        for col in range(4):
            out[row * 4 + col] = (
                a[row * 4 + 0] * b[0 * 4 + col]
                + a[row * 4 + 1] * b[1 * 4 + col]
                + a[row * 4 + 2] * b[2 * 4 + col]
                + a[row * 4 + 3] * b[3 * 4 + col]
            )
    return out

def _apply_mat4(m: List[float], x: float, y: float, z: float) -> Tuple[float, float, float]:
    nx = x * m[0] + y * m[4] + z * m[8] + m[12]
    ny = x * m[1] + y * m[5] + z * m[9] + m[13]
    nz = x * m[2] + y * m[6] + z * m[10] + m[14]
    return nx, ny, nz


def _apply_mat3(m: List[float], x: float, y: float, z: float) -> Tuple[float, float, float]:
    nx = x * m[0] + y * m[4] + z * m[8]
    ny = x * m[1] + y * m[5] + z * m[9]
    nz = x * m[2] + y * m[6] + z * m[10]
    return nx, ny, nz

def _matrix_from_node(node: dict) -> List[float]:
    if "matrix" in node:
        m = node.get("matrix") or []
        if len(m) == 16:
            return [float(v) for v in m]
    t = node.get("translation") or [0.0, 0.0, 0.0]
    r = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    s = node.get("scale") or [1.0, 1.0, 1.0]
    tx, ty, tz = [float(v) for v in t]
    rx, ry, rz, rw = [float(v) for v in r]
    sx, sy, sz = [float(v) for v in s]
    # Quaternion to matrix
    xx = rx * rx
    yy = ry * ry
    zz = rz * rz
    xy = rx * ry
    xz = rx * rz
    yz = ry * rz
    wx = rw * rx
    wy = rw * ry
    wz = rw * rz
    m00 = 1.0 - 2.0 * (yy + zz)
    m01 = 2.0 * (xy - wz)
    m02 = 2.0 * (xz + wy)
    m10 = 2.0 * (xy + wz)
    m11 = 1.0 - 2.0 * (xx + zz)
    m12 = 2.0 * (yz - wx)
    m20 = 2.0 * (xz - wy)
    m21 = 2.0 * (yz + wx)
    m22 = 1.0 - 2.0 * (xx + yy)
    return [
        m00 * sx, m01 * sy, m02 * sz, 0.0,
        m10 * sx, m11 * sy, m12 * sz, 0.0,
        m20 * sx, m21 * sy, m22 * sz, 0.0,
        tx, ty, tz, 1.0,
    ]


def _gltf_collect_nodes(gltf: dict, node_indices: List[int], parent: List[float]) -> List[List[float]]:
    out: List[List[float]] = []
    nodes = gltf.get("nodes", []) or []
    for idx in node_indices:
        if idx < 0 or idx >= len(nodes):
            continue
        node = nodes[idx]
        local = _matrix_from_node(node)
        world = _mul_mat4(parent, local)
        out.append((node, world))
        children = node.get("children") or []
        out.extend(_gltf_collect_nodes(gltf, list(children), world))
    return out

def load_gltf_mesh_arrays(path: Path) -> Tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    if np is None:
        raise RuntimeError("numpy unavailable")
    if path.suffix.lower() == ".glb":
        gltf, buffers = _read_glb(path)
    else:
        gltf, buffers = _read_gltf(path)
    meshes = gltf.get("meshes", []) or []
    nodes = gltf.get("nodes", []) or []
    scene_index = gltf.get("scene", 0) or 0
    scenes = gltf.get("scenes", []) or []
    node_roots = []
    if scenes and scene_index < len(scenes):
        node_roots = scenes[scene_index].get("nodes", []) or []
    elif nodes:
        node_roots = list(range(len(nodes)))

    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    node_pairs = _gltf_collect_nodes(gltf, node_roots, identity)

    out_pos: List[float] = []
    out_uv: List[float] = []
    out_norm: List[float] = []

    for node, world in node_pairs:
        mesh_index = node.get("mesh")
        if mesh_index is None or mesh_index >= len(meshes):
            continue
        mesh = meshes[mesh_index]
        for prim in mesh.get("primitives", []) or []:
            attrs = prim.get("attributes") or {}
            pos_accessor = attrs.get("POSITION")
            if pos_accessor is None:
                continue
            positions = _read_accessor(gltf, buffers, int(pos_accessor))
            normals = None
            if "NORMAL" in attrs:
                normals = _read_accessor(gltf, buffers, int(attrs["NORMAL"]))
            uvs = None
            if "TEXCOORD_0" in attrs:
                uvs = _read_accessor(gltf, buffers, int(attrs["TEXCOORD_0"]))
            indices = None
            if "indices" in prim:
                idx_data = _read_accessor(gltf, buffers, int(prim["indices"]))
                indices = [int(i) for i in idx_data]
            tri_mode = int(prim.get("mode", 4))
            if tri_mode != 4:
                continue
            if indices is None:
                indices = list(range(len(positions) // 3))
            for idx in indices:
                base = idx * 3
                vx, vy, vz = positions[base], positions[base + 1], positions[base + 2]
                vx, vy, vz = _apply_mat4(world, vx, vy, vz)
                out_pos.extend([vx, vy, vz])
                if uvs is not None:
                    ub = idx * 2
                    if ub + 1 < len(uvs):
                        out_uv.extend([uvs[ub], 1.0 - uvs[ub + 1]])
                    else:
                        out_uv.extend([0.0, 0.0])
                else:
                    out_uv.extend([0.0, 0.0])
                if normals is not None:
                    nb = idx * 3
                    if nb + 2 < len(normals):
                        nx, ny, nz = normals[nb], normals[nb + 1], normals[nb + 2]
                    else:
                        nx, ny, nz = 0.0, 0.0, 0.0
                    nx, ny, nz = _apply_mat3(world, nx, ny, nz)
                    nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
                    if nlen > 1e-6:
                        nx, ny, nz = nx / nlen, ny / nlen, nz / nlen
                    out_norm.extend([nx, ny, nz])
                else:
                    out_norm.extend([0.0, 0.0, 0.0])

    if not out_pos:
        return np.zeros((0, 3), dtype="f4"), np.zeros((0, 3), dtype="f4"), np.zeros((0, 2), dtype="f4")

    pos_arr = np.array(out_pos, dtype="f4").reshape(-1, 3)
    norm_arr = np.array(out_norm, dtype="f4").reshape(-1, 3)
    uv_arr = np.array(out_uv, dtype="f4").reshape(-1, 2)

    if not np.any(norm_arr):
        for i in range(0, pos_arr.shape[0], 3):
            a, b, c = pos_arr[i:i + 3]
            n = np.cross(b - a, c - a)
            length = float(np.linalg.norm(n))
            if length > 1e-6:
                n = n / length
            norm_arr[i:i + 3] = n

    return pos_arr, norm_arr, uv_arr


def load_gltf_model(path: Path) -> ModelData:
    if path.suffix.lower() == ".glb":
        gltf, buffers = _read_glb(path)
    else:
        gltf, buffers = _read_gltf(path)
    meshes = gltf.get("meshes", []) or []
    nodes = gltf.get("nodes", []) or []
    scene_index = gltf.get("scene", 0) or 0
    scenes = gltf.get("scenes", []) or []
    node_roots = []
    if scenes and scene_index < len(scenes):
        node_roots = scenes[scene_index].get("nodes", []) or []
    elif nodes:
        node_roots = list(range(len(nodes)))

    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    node_pairs = _gltf_collect_nodes(gltf, node_roots, identity)

    vertices: List[float] = []
    bounds = [math.inf, math.inf, math.inf, -math.inf, -math.inf, -math.inf]

    for node, world in node_pairs:
        mesh_index = node.get("mesh")
        if mesh_index is None or mesh_index >= len(meshes):
            continue
        mesh = meshes[mesh_index]
        for prim in mesh.get("primitives", []) or []:
            attrs = prim.get("attributes") or {}
            pos_accessor = attrs.get("POSITION")
            if pos_accessor is None:
                continue
            positions = _read_accessor(gltf, buffers, int(pos_accessor))
            indices = None
            if "indices" in prim:
                idx_data = _read_accessor(gltf, buffers, int(prim["indices"]))
                indices = [int(i) for i in idx_data]
            tri_mode = int(prim.get("mode", 4))
            if tri_mode != 4:
                continue
            primitive_vertices: List[float] = []
            if indices:
                for idx in indices:
                    base = idx * 3
                    x, y, z = positions[base], positions[base + 1], positions[base + 2]
                    x, y, z = _apply_mat4(world, x, y, z)
                    primitive_vertices.extend([x, y, z])
            else:
                for i in range(0, len(positions), 3):
                    x, y, z = positions[i], positions[i + 1], positions[i + 2]
                    x, y, z = _apply_mat4(world, x, y, z)
                    primitive_vertices.extend([x, y, z])
            for i in range(0, len(primitive_vertices), 3):
                vx, vy, vz = (
                    primitive_vertices[i],
                    primitive_vertices[i + 1],
                    primitive_vertices[i + 2],
                )
                bounds[0] = min(bounds[0], vx)
                bounds[1] = min(bounds[1], vy)
                bounds[2] = min(bounds[2], vz)
                bounds[3] = max(bounds[3], vx)
                bounds[4] = max(bounds[4], vy)
                bounds[5] = max(bounds[5], vz)
            vertices.extend(primitive_vertices)

    if not vertices:
        bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    return ModelData(vertices=vertices, bounds=tuple(bounds))

def load_model(path: Path) -> Optional["ModelData"]:
    ext = path.suffix.lower()
    loader = _MODEL_LOADERS.get(ext)
    if loader is None:
        return None
    try:
        return loader(path)
    except Exception:
        return None

def _decode_data_uri(uri: str) -> bytes:
    header, _, data = uri.partition(",")
    if "base64" in header:
        return base64.b64decode(data)
    return data.encode("utf-8")
