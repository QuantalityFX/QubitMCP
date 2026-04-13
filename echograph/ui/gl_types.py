# echograph/ui/gl_types.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import numpy as np
except Exception:
    np = None


@dataclass
class ModelData:
    vertices: List[float]
    bounds: Tuple[float, float, float, float, float, float]


@dataclass
class SubMeshData:
    points: "np.ndarray"
    normals: "np.ndarray"
    uvs: "np.ndarray"
    name: Optional[str] = None
    texture_path: Optional[Path] = None
    texture_image: Optional[object] = None
    base_color: Optional[Tuple[float, float, float, float]] = None


@dataclass
class MeshArrays:
    points: "np.ndarray"
    normals: "np.ndarray"
    uvs: "np.ndarray"
    texture_path: Optional[Path] = None
    texture_image: Optional[object] = None
    base_color: Optional[Tuple[float, float, float, float]] = None
    submeshes: Optional[List["SubMeshData"]] = None
