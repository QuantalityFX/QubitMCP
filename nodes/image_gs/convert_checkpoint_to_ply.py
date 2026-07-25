from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch


C0 = 0.28209479177387814


def _tensor(state: dict, name: str) -> torch.Tensor:
    for key in (name, f"module.{name}"):
        value = state.get(key)
        if isinstance(value, torch.Tensor):
            return value.detach().cpu()
    raise KeyError(f"Checkpoint does not contain '{name}'.")


def _normalise_quats(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=np.float32)
    norm = np.linalg.norm(q, axis=1, keepdims=True)
    return q / np.maximum(norm, np.float32(1.0e-8))


def _write_gaussian_splat_ply(path: Path, splats: np.ndarray) -> None:
    arr = np.asarray(splats, dtype=np.float32)
    if arr.ndim != 2 or int(arr.shape[1]) != 15:
        raise ValueError("Gaussian splat PLY writer expects an Nx15 array.")

    rgb = np.clip(arr[:, 3:6], 0.0, 1.0)
    alpha = np.clip(arr[:, 6], 1.0e-6, 1.0 - 1.0e-6)
    radius = np.maximum(arr[:, 7], 1.0e-8)
    scale3 = np.maximum(arr[:, 8:11], 1.0e-8)
    quat_xyzw = _normalise_quats(arr[:, 11:15])

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
        "comment generated_by QubitMCP image_gs_splat\n"
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

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(header.encode("ascii"))
        handle.write(payload.tobytes(order="C"))


def _rgb_from_feat(feat: np.ndarray) -> np.ndarray:
    feat = np.asarray(feat, dtype=np.float32)
    if feat.ndim == 1:
        feat = feat.reshape(-1, 1)
    if feat.shape[1] >= 3:
        rgb = feat[:, :3]
    elif feat.shape[1] == 2:
        rgb = np.column_stack([feat[:, 0], feat[:, 1], feat[:, 1]])
    elif feat.shape[1] == 1:
        rgb = np.repeat(feat[:, :1], 3, axis=1)
    else:
        rgb = np.ones((feat.shape[0], 3), dtype=np.float32)
    return np.clip(rgb, 0.0, 1.0)


def convert_checkpoint(
    ckpt_path: Path,
    out_path: Path,
    *,
    width: int,
    height: int,
    sheet_scale: float,
    radius_scale: float,
    alpha: float,
    inverse_scale: bool,
    max_splats: int,
) -> int:
    checkpoint = torch.load(str(ckpt_path), map_location="cpu")
    state = checkpoint.get("state_dict") if isinstance(checkpoint, dict) else None
    if not isinstance(state, dict):
        raise ValueError("Image-GS checkpoint does not contain a state_dict.")

    xy = _tensor(state, "xy").numpy().astype(np.float32, copy=False)
    scale = _tensor(state, "scale").numpy().astype(np.float32, copy=False)
    rot = _tensor(state, "rot").numpy().astype(np.float32, copy=False)
    feat = _tensor(state, "feat").numpy().astype(np.float32, copy=False)

    count = int(min(xy.shape[0], scale.shape[0], rot.shape[0], feat.shape[0]))
    if count <= 0:
        raise ValueError("Image-GS checkpoint does not contain any Gaussians.")
    if max_splats > 0 and count > int(max_splats):
        indices = np.linspace(0, count - 1, int(max_splats), dtype=np.int64)
        xy = xy[indices]
        scale = scale[indices]
        rot = rot[indices]
        feat = feat[indices]
        count = int(max_splats)
    else:
        xy = xy[:count]
        scale = scale[:count]
        rot = rot[:count]
        feat = feat[:count]

    img_w = max(1, int(width))
    img_h = max(1, int(height))
    aspect = float(img_w) / float(img_h)
    sheet = max(0.001, float(sheet_scale))

    pos = np.zeros((count, 3), dtype=np.float32)
    pos[:, 0] = (xy[:, 0] - 0.5) * aspect * sheet
    pos[:, 1] = (0.5 - xy[:, 1]) * sheet

    scale = np.maximum(scale[:, :2], np.float32(1.0e-6))
    if inverse_scale:
        scale_px = 1.0 / scale
    else:
        scale_px = scale
    scale_px = np.clip(scale_px, 0.05, max(img_w, img_h) * 0.25)
    pixel_unit = sheet / float(img_h)
    axis_x = np.maximum(scale_px[:, 0] * pixel_unit * float(radius_scale), 1.0e-6)
    axis_y = np.maximum(scale_px[:, 1] * pixel_unit * float(radius_scale), 1.0e-6)
    radius = np.sqrt(axis_x * axis_y).astype(np.float32)
    axis_z = np.maximum(radius * 0.04, 1.0e-6).astype(np.float32)
    scale3 = np.column_stack([axis_x / radius, axis_y / radius, axis_z / radius]).astype(np.float32)

    theta = rot.reshape(-1).astype(np.float32)
    half = theta * 0.5
    quat = np.column_stack(
        [
            np.zeros_like(half),
            np.zeros_like(half),
            np.sin(half),
            np.cos(half),
        ]
    ).astype(np.float32)

    rgb = _rgb_from_feat(feat)
    alpha_arr = np.full((count, 1), np.clip(float(alpha), 1.0e-4, 1.0 - 1.0e-4), dtype=np.float32)
    splats = np.concatenate(
        [pos, rgb, alpha_arr, radius.reshape(-1, 1), scale3, quat],
        axis=1,
    ).astype(np.float32, copy=False)

    _write_gaussian_splat_ply(out_path, splats)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert an Image-GS checkpoint to a QubitMCP splat PLY.")
    parser.add_argument("--ckpt", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int)
    parser.add_argument("--height", required=True, type=int)
    parser.add_argument("--sheet-scale", default=2.0, type=float)
    parser.add_argument("--radius-scale", default=1.0, type=float)
    parser.add_argument("--alpha", default=0.92, type=float)
    parser.add_argument("--inverse-scale", default="1")
    parser.add_argument("--max-splats", default=200000, type=int)
    args = parser.parse_args()

    inverse_scale = str(args.inverse_scale).strip().lower() not in {"0", "false", "no", "off"}
    count = convert_checkpoint(
        args.ckpt,
        args.out,
        width=args.width,
        height=args.height,
        sheet_scale=args.sheet_scale,
        radius_scale=args.radius_scale,
        alpha=args.alpha,
        inverse_scale=inverse_scale,
        max_splats=args.max_splats,
    )
    print(f"Wrote {count:d} splats to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
