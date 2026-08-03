from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Iterator

import numpy as np

try:
    from PySide6 import QtCore, QtGui
except Exception:
    from PySide2 import QtCore, QtGui  # type: ignore

try:
    from nodes.image_gs.splat_ply_writer import (
        encode_gaussian_splat_payload,
        gaussian_splat_ply_header,
    )
except Exception:
    from splat_ply_writer import encode_gaussian_splat_payload, gaussian_splat_ply_header  # type: ignore


def _qimage_format(name: str):
    direct = getattr(QtGui.QImage, name, None)
    if direct is not None:
        return direct
    enum = getattr(QtGui.QImage, "Format", None)
    return getattr(enum, name, None) if enum is not None else None


def _image_to_rgba_array(path: Path, *, max_splats: int = 0) -> tuple[np.ndarray, int, int, int, int]:
    image = QtGui.QImage(str(path))
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise RuntimeError(f"Could not read source image: {path}")

    source_w = int(image.width())
    source_h = int(image.height())
    width = source_w
    height = source_h
    if int(max_splats) > 0 and source_w * source_h > int(max_splats):
        scale = math.sqrt(float(max(1, int(max_splats))) / float(source_w * source_h))
        width = max(1, int(math.floor(source_w * scale)))
        height = max(1, int(math.floor(source_h * scale)))
        while width * height > int(max_splats) and (width > 1 or height > 1):
            if width >= height and width > 1:
                width -= 1
            elif height > 1:
                height -= 1
            else:
                break
        image = image.scaled(width, height, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)

    try:
        srgb = QtGui.QColorSpace(QtGui.QColorSpace.SRgb)
        converted = image.convertedToColorSpace(srgb) if hasattr(image, "convertedToColorSpace") else None
        if converted is not None and not converted.isNull():
            image = converted
        else:
            image.convertToColorSpace(srgb)
    except Exception:
        pass

    fmt = _qimage_format("Format_RGBA8888")
    if fmt is None:
        raise RuntimeError("Qt build does not expose QImage.Format_RGBA8888.")
    rgba = image.convertToFormat(fmt)
    if rgba.isNull():
        raise RuntimeError(f"Could not convert source image to RGBA8888: {path}")

    width = int(rgba.width())
    height = int(rgba.height())
    bytes_per_line = int(rgba.bytesPerLine())
    byte_count = int(bytes_per_line * height)
    try:
        bits = rgba.constBits()
    except Exception:
        bits = rgba.bits()
    try:
        bits.setsize(byte_count)
    except Exception:
        pass
    raw = np.frombuffer(bits, dtype=np.uint8, count=byte_count)
    rows = raw.reshape(height, bytes_per_line)
    packed = rows[:, : width * 4].reshape(height, width, 4).copy()
    return packed, source_w, source_h, width, height


def _initial_interleave_stride(width: int, height: int) -> int:
    stride = 1
    max_dim = max(1, int(width), int(height))
    while stride * 2 < max_dim:
        stride *= 2
    return stride


def _visible_pixel_chunks(
    visible: np.ndarray,
    *,
    chunk_rows: int,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    height, width = visible.shape
    start_stride = _initial_interleave_stride(width, height)
    chunk_rows = max(1, int(chunk_rows))
    stride = start_stride

    while stride >= 1:
        ys_all = np.arange(0, height, stride, dtype=np.int64)
        xs = np.arange(0, width, stride, dtype=np.int64)
        if ys_all.size <= 0 or xs.size <= 0:
            stride //= 2
            continue

        prev_stride = stride * 2
        x_from_previous_pass = (xs % prev_stride) == 0 if stride < start_stride else None
        for y_start in range(0, int(ys_all.size), chunk_rows):
            ys = ys_all[y_start : y_start + chunk_rows]
            mask = visible[np.ix_(ys, xs)]
            if stride < start_stride:
                y_from_previous_pass = (ys % prev_stride) == 0
                mask = mask & ~(y_from_previous_pass[:, None] & x_from_previous_pass[None, :])
            if not np.any(mask):
                continue
            yy, xx = np.nonzero(mask)
            yield ys[yy], xs[xx]

        stride //= 2


def convert_image_to_ply(
    image_path: Path,
    out_path: Path,
    *,
    sheet_scale: float,
    radius_scale: float,
    alpha: float,
    max_splats: int,
    alpha_threshold: float,
    coverage_boost: float,
    z_axis_ratio: float,
    chunk_rows: int = 96,
) -> tuple[int, str]:
    rgba, source_w, source_h, width, height = _image_to_rgba_array(image_path, max_splats=max_splats)
    alpha_src = rgba[:, :, 3].astype(np.float32) / np.float32(255.0)
    threshold = np.float32(max(0.0, min(1.0, float(alpha_threshold))))
    visible = alpha_src > threshold
    count = int(np.count_nonzero(visible))
    skipped = int(width * height - count)
    if count <= 0:
        raise RuntimeError("Source image has no visible pixels after alpha filtering.")

    sheet = max(0.001, float(sheet_scale))
    aspect = float(width) / float(max(1, height))
    pixel_unit = sheet / float(max(1, height))
    axis_px = max(0.01, float(radius_scale)) * max(1.0, float(coverage_boost))
    axis_xy = np.float32(max(1.0e-6, axis_px * pixel_unit))
    axis_z = np.float32(max(1.0e-6, axis_xy * max(0.04, float(z_axis_ratio))))
    alpha_mul = np.float32(max(0.001, min(0.999, float(alpha))))

    x_centers = ((np.arange(width, dtype=np.float32) + np.float32(0.5)) / np.float32(width) - np.float32(0.5))
    x_centers = x_centers * np.float32(aspect * sheet)
    identity_quat = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
    chunk_rows = max(1, int(chunk_rows))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        handle.write(
            gaussian_splat_ply_header(
                count,
                comment="generated_by QubitMCP image_gs_splat exact_pixel",
            )
        )
        for yy, xx in _visible_pixel_chunks(visible, chunk_rows=chunk_rows):
            n = int(xx.shape[0])
            source_alpha = alpha_src[yy, xx]
            rgb = rgba[yy, xx, :3].astype(np.float32) / np.float32(255.0)
            row_indices = yy.astype(np.float32) + np.float32(0.5)

            pos = np.zeros((n, 3), dtype=np.float32)
            pos[:, 0] = x_centers[xx]
            pos[:, 1] = (np.float32(0.5) - (row_indices / np.float32(height))) * np.float32(sheet)

            axes = np.empty((n, 3), dtype=np.float32)
            axes[:, 0] = axis_xy
            axes[:, 1] = axis_xy
            axes[:, 2] = axis_z
            alpha_values = np.clip(source_alpha * alpha_mul, np.float32(1.0e-6), np.float32(1.0 - 1.0e-6))
            quats = np.broadcast_to(identity_quat, (n, 4))
            payload = encode_gaussian_splat_payload(pos, rgb, alpha_values, axes, quats)
            handle.write(payload.tobytes(order="C"))

    size_mb = float(out_path.stat().st_size) / (1024.0 * 1024.0) if out_path.exists() else 0.0
    downsampled = (int(source_w) != int(width)) or (int(source_h) != int(height))
    stats = (
        f"source={int(source_w)}x{int(source_h)}, exported={int(width)}x{int(height)}, "
        f"skipped_transparent={skipped}, downsampled={'yes' if downsampled else 'no'}, "
        "order=interleaved, "
        f"axis_px={axis_px:.3f}, alpha={float(alpha_mul):.3f}, "
        f"coverage_boost={max(1.0, float(coverage_boost)):.3f}, "
        f"z_axis_ratio={max(0.04, float(z_axis_ratio)):.3f}, "
        f"size_mb={size_mb:.2f}"
    )
    return count, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert an image directly to a front-facing Gaussian splat PLY.")
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--sheet-scale", default=2.0, type=float)
    parser.add_argument("--radius-scale", default=1.0, type=float)
    parser.add_argument("--alpha", default=0.92, type=float)
    parser.add_argument("--max-splats", default=0, type=int)
    parser.add_argument("--alpha-threshold", default=0.003, type=float)
    parser.add_argument("--coverage-boost", default=1.15, type=float)
    parser.add_argument("--z-axis-ratio", default=0.25, type=float)
    parser.add_argument("--chunk-rows", default=96, type=int)
    args = parser.parse_args()

    count, stats = convert_image_to_ply(
        args.image,
        args.out,
        sheet_scale=args.sheet_scale,
        radius_scale=args.radius_scale,
        alpha=args.alpha,
        max_splats=args.max_splats,
        alpha_threshold=args.alpha_threshold,
        coverage_boost=args.coverage_boost,
        z_axis_ratio=args.z_axis_ratio,
        chunk_rows=args.chunk_rows,
    )
    print(f"Wrote {count:d} splats to {args.out}; {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
