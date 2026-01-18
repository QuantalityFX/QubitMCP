# echograph/util/splats_io.py
from __future__ import annotations

from typing import Any


def load_splats_ply(path: str, n: int = 200_000) -> Any:
    """
    Load a Gaussian-splat .ply and return Nx15 float32 array:
    [pos3, col4, rad1, scale3, quat4] (your current convention).

    This is a thin wrapper around the current loader so we can later
    replace the implementation without touching UI / GL code.
    """
    from echograph.util.load_gs_ply import load_gs_ply
    return load_gs_ply(path, n=n)
