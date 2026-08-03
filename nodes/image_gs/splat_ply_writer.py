from __future__ import annotations

from pathlib import Path

import numpy as np


C0 = 0.28209479177387814


def _normalise_quats(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=np.float32)
    norm = np.linalg.norm(q, axis=1, keepdims=True)
    return q / np.maximum(norm, np.float32(1.0e-8))


def gaussian_splat_ply_header(count: int, *, comment: str = "generated_by QubitMCP image_gs_splat") -> bytes:
    text = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"comment {comment}\n"
        f"element vertex {int(count)}\n"
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
    return text.encode("ascii")


def encode_gaussian_splat_payload(
    pos: np.ndarray,
    rgb: np.ndarray,
    alpha: np.ndarray,
    axes: np.ndarray,
    quat_xyzw: np.ndarray,
) -> np.ndarray:
    pos = np.asarray(pos, dtype=np.float32).reshape(-1, 3)
    count = int(pos.shape[0])
    rgb = np.asarray(rgb, dtype=np.float32).reshape(count, 3)
    alpha = np.asarray(alpha, dtype=np.float32).reshape(count)
    axes = np.asarray(axes, dtype=np.float32).reshape(count, 3)
    quat_xyzw = _normalise_quats(np.asarray(quat_xyzw, dtype=np.float32).reshape(count, 4))

    rgb = np.clip(rgb, 0.0, 1.0)
    alpha = np.clip(alpha, 1.0e-6, 1.0 - 1.0e-6)
    axes = np.maximum(axes, 1.0e-8)

    fdc = (rgb - 0.5) / C0
    opacity = np.log(alpha / (1.0 - alpha)).reshape(-1, 1)
    log_scales = np.log(axes)
    rot_wxyz = np.column_stack(
        [quat_xyzw[:, 3], quat_xyzw[:, 0], quat_xyzw[:, 1], quat_xyzw[:, 2]]
    )
    return np.concatenate([pos, fdc, opacity, log_scales, rot_wxyz], axis=1).astype("<f4", copy=False)


def write_gaussian_splat_ply(
    path: Path,
    splats: np.ndarray,
    *,
    comment: str = "generated_by QubitMCP image_gs_splat",
) -> None:
    arr = np.asarray(splats, dtype=np.float32)
    if arr.ndim != 2 or int(arr.shape[1]) != 15:
        raise ValueError("Gaussian splat PLY writer expects an Nx15 array.")

    rgb = np.clip(arr[:, 3:6], 0.0, 1.0)
    alpha = np.clip(arr[:, 6], 1.0e-6, 1.0 - 1.0e-6)
    radius = np.maximum(arr[:, 7], 1.0e-8)
    scale3 = np.maximum(arr[:, 8:11], 1.0e-8)
    axes = np.maximum(radius.reshape(-1, 1) * scale3, 1.0e-8)
    payload = encode_gaussian_splat_payload(arr[:, 0:3], rgb, alpha, axes, arr[:, 11:15])

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(gaussian_splat_ply_header(int(payload.shape[0]), comment=comment))
        handle.write(payload.tobytes(order="C"))
