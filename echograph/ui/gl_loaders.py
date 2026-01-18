# echograph/ui/gl_loaders.py
from __future__ import annotations

import os
import ctypes
import io
from pathlib import Path
from typing import List, Optional

from .gl_types import MeshArrays, SubMeshData

try:
    import numpy as np
except Exception:
    np = None

try:
    from PIL import Image as PILImage
except Exception:
    PILImage = None

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
