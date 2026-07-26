from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch


C0 = 0.28209479177387814
_EPS = np.float32(1.0e-8)


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


def _bool_arg(value: str) -> bool:
    return str(value or "").strip().lower() not in {"0", "false", "no", "off"}


def _auto_max_axis_px(width: int, height: int) -> float:
    return float(np.clip(min(max(1, int(width)), max(1, int(height))) * 0.0125, 6.0, 24.0))


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
    min_axis_px: float,
    max_axis_px: float,
    max_anisotropy: float,
    drop_scale_outliers: bool,
    outlier_axis_factor: float,
    coverage_boost: float,
    z_axis_ratio: float,
) -> tuple[int, str]:
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
    xy = xy[:count]
    scale = scale[:count]
    rot = rot[:count]
    feat = feat[:count]

    img_w = max(1, int(width))
    img_h = max(1, int(height))
    aspect = float(img_w) / float(img_h)
    sheet = max(0.001, float(sheet_scale))

    scale_raw = scale[:, :2].astype(np.float32, copy=False)
    valid = (
        np.isfinite(xy).all(axis=1)
        & np.isfinite(scale_raw).all(axis=1)
        & np.isfinite(rot.reshape(-1))
        & np.isfinite(feat).all(axis=1)
    )
    margin = np.float32(0.02)
    valid &= (
        (xy[:, 0] >= -margin)
        & (xy[:, 0] <= 1.0 + margin)
        & (xy[:, 1] >= -margin)
        & (xy[:, 1] <= 1.0 + margin)
    )
    dropped_invalid = int(count - int(valid.sum()))
    if not np.any(valid):
        raise ValueError("Image-GS checkpoint does not contain any finite in-bounds Gaussians.")
    xy = xy[valid]
    scale_raw = scale_raw[valid]
    rot = rot[valid]
    feat = feat[valid]

    scale_abs = np.maximum(np.abs(scale_raw), _EPS)
    if inverse_scale:
        axis_px = 1.0 / scale_abs
    else:
        axis_px = scale_abs
    axis_px = axis_px.astype(np.float32, copy=False) * np.float32(max(0.001, float(radius_scale)))
    axis_px = np.where(np.isfinite(axis_px), axis_px, np.float32(0.0))

    min_axis = np.float32(max(0.05, float(min_axis_px)))
    axis_cap = np.float32(float(max_axis_px) if float(max_axis_px) > 0.0 else _auto_max_axis_px(img_w, img_h))
    axis_cap = np.float32(max(float(min_axis) * 2.0, float(axis_cap)))
    anisotropy_cap = np.float32(max(1.0, float(max_anisotropy)))
    outlier_factor = np.float32(max(1.0, float(outlier_axis_factor)))
    axis_min_pre = np.maximum(np.min(axis_px, axis=1), min_axis * np.float32(0.05))
    axis_max_pre = np.max(axis_px, axis=1)
    ratio_pre = axis_max_pre / axis_min_pre
    outlier = (axis_max_pre > axis_cap * outlier_factor) | (ratio_pre > anisotropy_cap * outlier_factor)
    dropped_outliers = 0
    if drop_scale_outliers and np.any(outlier):
        keep = ~outlier
        dropped_outliers = int(outlier.sum())
        xy = xy[keep]
        axis_px = axis_px[keep]
        rot = rot[keep]
        feat = feat[keep]
        if xy.shape[0] <= 0:
            raise ValueError("All Image-GS Gaussians were rejected as scale outliers.")

    clamped_axis = (axis_px < min_axis) | (axis_px > axis_cap)
    axis_px = np.clip(axis_px, min_axis, axis_cap)
    minor = np.minimum(axis_px[:, 0], axis_px[:, 1])
    major_limit = minor * anisotropy_cap
    too_aniso_x = axis_px[:, 0] > major_limit
    too_aniso_y = axis_px[:, 1] > major_limit
    anisotropy_clamped = int(np.count_nonzero(too_aniso_x | too_aniso_y))
    axis_px[:, 0] = np.where(too_aniso_x, major_limit, axis_px[:, 0])
    axis_px[:, 1] = np.where(too_aniso_y, major_limit, axis_px[:, 1])
    clamped_axes = int(np.count_nonzero(clamped_axis)) + anisotropy_clamped

    boost = np.float32(max(1.0, float(coverage_boost)))
    if boost > 1.0:
        axis_px = np.minimum(axis_px * boost, axis_cap)

    count = int(xy.shape[0])
    if max_splats > 0 and count > int(max_splats):
        indices = np.linspace(0, count - 1, int(max_splats), dtype=np.int64)
        xy = xy[indices]
        axis_px = axis_px[indices]
        rot = rot[indices]
        feat = feat[indices]
        count = int(max_splats)

    pos = np.zeros((count, 3), dtype=np.float32)
    pos[:, 0] = (xy[:, 0] - 0.5) * aspect * sheet
    pos[:, 1] = (0.5 - xy[:, 1]) * sheet

    pixel_unit = sheet / float(img_h)
    axis_x = np.maximum(axis_px[:, 0] * pixel_unit, 1.0e-6)
    axis_y = np.maximum(axis_px[:, 1] * pixel_unit, 1.0e-6)
    radius = np.sqrt(axis_x * axis_y).astype(np.float32)
    axis_z = np.maximum(radius * np.float32(max(0.04, float(z_axis_ratio))), 1.0e-6).astype(np.float32)
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
    stats = (
        f"axis_cap_px={float(axis_cap):.2f}, min_axis_px={float(min_axis):.2f}, "
        f"max_anisotropy={float(anisotropy_cap):.2f}, clamped_axes={clamped_axes}, "
        f"dropped_invalid={dropped_invalid}, dropped_outliers={dropped_outliers}, "
        f"coverage_boost={float(boost):.2f}, z_axis_ratio={float(max(0.04, float(z_axis_ratio))):.2f}"
    )
    return count, stats


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
    parser.add_argument("--min-axis-px", default=0.35, type=float)
    parser.add_argument("--max-axis-px", default=0.0, type=float)
    parser.add_argument("--max-anisotropy", default=8.0, type=float)
    parser.add_argument("--drop-scale-outliers", default="1")
    parser.add_argument("--outlier-axis-factor", default=8.0, type=float)
    parser.add_argument("--coverage-boost", default=1.0, type=float)
    parser.add_argument("--z-axis-ratio", default=0.04, type=float)
    args = parser.parse_args()

    inverse_scale = _bool_arg(args.inverse_scale)
    count, stats = convert_checkpoint(
        args.ckpt,
        args.out,
        width=args.width,
        height=args.height,
        sheet_scale=args.sheet_scale,
        radius_scale=args.radius_scale,
        alpha=args.alpha,
        inverse_scale=inverse_scale,
        max_splats=args.max_splats,
        min_axis_px=args.min_axis_px,
        max_axis_px=args.max_axis_px,
        max_anisotropy=args.max_anisotropy,
        drop_scale_outliers=_bool_arg(args.drop_scale_outliers),
        outlier_axis_factor=args.outlier_axis_factor,
        coverage_boost=args.coverage_boost,
        z_axis_ratio=args.z_axis_ratio,
    )
    print(f"Wrote {count:d} splats to {args.out}; {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
