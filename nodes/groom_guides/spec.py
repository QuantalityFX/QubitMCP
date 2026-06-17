from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import numpy as np
except Exception:
    np = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec


SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb"}
GROOM_GUIDES_BODY_H = 190
GROOM_GUIDES_NODE_W = 260
GROOM_GUIDES_SEGMENTS_HARD_MAX = 512
GROOM_GUIDES_HIDDEN_PARAMS = [
    "mask",
    "source",
    "path",
    "guides_path",
    "threshold",
    "length",
    "seed",
    "guide_count",
    "segments",
    "debug_log",
]
GROOM_GUIDES_DEBUG_KEYS = (
    "mask_node",
    "source_owner",
    "source_path",
    "source_ext",
    "source_exists",
    "mask_size",
    "mask_max_value",
    "mask_effective_threshold",
    "mask_candidate_pixels",
    "vertex_count",
    "triangle_count",
    "uv_triangle_count",
    "uv_min",
    "uv_max",
    "requested_guides",
    "generated_curves",
    "line_segment_count",
    "curve_bounds_min",
    "curve_bounds_max",
    "guide_length",
    "attempts",
    "uv_misses",
    "guides_path",
)


@dataclass(frozen=True)
class GroomGuidesBuildOutcome:
    asset: Optional[dict[str, Any]]
    source_assets: tuple[dict[str, Any], ...]
    status: str
    detail: str
    debug: Optional[dict[str, Any]] = None


def _param_value(model, name: str, default: str = "") -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value", default) or default)
    return default


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
            setattr(model, "params", params)
        except Exception:
            return
    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter(name, str(value), rebuild=False, notify_scene=bool(notify_scene))
            return
        except Exception:
            pass
    _ensure_param(node_item, name, str(value))
    model = getattr(node_item, "model", None)
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            return


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = param
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    hidden = {part.strip().lower() for part in str(entry.get("value") or "").split(",") if part.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        setattr(model, "params", params)
    except Exception:
        pass


def _edge_dst_name(edge) -> str:
    return str(
        getattr(edge, "dst_port_name", None)
        or getattr(edge, "dst_label", None)
        or getattr(edge, "dst_name", None)
        or ""
    ).strip()


def _ordered_in_edges(scene, item) -> list:
    if scene is None or item is None:
        return []
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _connected_input_item(node_item, names: set[str]):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    chosen = None
    for edge in _ordered_in_edges(scene, node_item):
        dst = _edge_dst_name(edge).lower()
        if dst in names:
            return getattr(edge, "src", None)
        if chosen is None:
            chosen = getattr(edge, "src", None)
    return chosen


def _resolve_window(node_item):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                return views[0].window()
        except Exception:
            pass
    try:
        active = QtWidgets.QApplication.activeWindow()
        if active is not None and active.isWindow():
            return active
    except Exception:
        pass
    return None


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or "groom_guides"


def _workflow_dir_for_node(node_item) -> Optional[Path]:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    workflow_path = getattr(scene, "_filename", None) if scene is not None else None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                workflow_path = getattr(views[0].window(), "_current_path", None) or workflow_path
        except Exception:
            pass
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _guides_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "groom_guides"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _logs_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    out = root / "logs"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _mesh_stamp(path: str) -> tuple[str, Optional[int], Optional[int]]:
    text = str(path or "")
    try:
        st = os.stat(text)
        return (
            text,
            int(getattr(st, "st_mtime_ns", int(float(st.st_mtime) * 1_000_000_000))),
            int(st.st_size),
        )
    except Exception:
        return (text, None, None)


def _output_path(
    node_item,
    source_path: str,
    mask_path: str,
    guide_count: int,
    points_per_curve: int,
    threshold: float,
    length_scale: float,
    seed: int,
) -> Path:
    model = getattr(node_item, "model", None)
    node_name = _sanitize_name(str(getattr(model, "name", "") or "groom_guides"))
    sig = hashlib.sha1(
        repr(
            (
                _mesh_stamp(source_path),
                _mesh_stamp(mask_path),
                int(guide_count),
                int(points_per_curve),
                round(float(threshold), 5),
                round(float(length_scale), 5),
                int(seed),
            )
        ).encode("utf-8")
    ).hexdigest()[:10]
    return _guides_dir(node_item) / f"{node_name}_{sig}.json"


def _mesh_arrays_tuple(mesh_arrays, hidden_submeshes=None):
    hidden = {str(name).strip().lower() for name in (hidden_submeshes or []) if str(name).strip()}
    submeshes = list(getattr(mesh_arrays, "submeshes", None) or []) if mesh_arrays is not None else []
    if hidden and submeshes:
        visible_submeshes = []
        for idx, sub in enumerate(submeshes):
            sub_name = str(getattr(sub, "name", "") or "").strip() or f"mesh_{int(idx)}"
            if sub_name.lower() not in hidden:
                visible_submeshes.append(sub)
        if not visible_submeshes:
            return None, None, None
        try:
            points = np.concatenate([np.asarray(getattr(sub, "points"), dtype="f4").reshape(-1, 3) for sub in visible_submeshes], axis=0)
            normals = np.concatenate([np.asarray(getattr(sub, "normals"), dtype="f4").reshape(-1, 3) for sub in visible_submeshes], axis=0)
            uvs = np.concatenate([np.asarray(getattr(sub, "uvs"), dtype="f4").reshape(-1, 2) for sub in visible_submeshes], axis=0)
            return points, normals, uvs
        except Exception:
            pass
    return getattr(mesh_arrays, "points", None), getattr(mesh_arrays, "normals", None), getattr(mesh_arrays, "uvs", None)


def _load_mesh_arrays(path: Path, hidden_submeshes=None):
    if np is None:
        return None, None, None
    try:
        from echograph.ui import gl_loaders
    except Exception:
        return None, None, None
    ext = path.suffix.lower()
    try:
        if ext == ".obj":
            pts, norms, uvs = gl_loaders.load_obj_mesh_arrays(path)
            return pts, norms, uvs
        if ext == ".fbx":
            arrays = gl_loaders.load_fbx_mesh_arrays_pyassimp(path)
            return _mesh_arrays_tuple(arrays, hidden_submeshes)
        if ext in {".gltf", ".glb"}:
            arrays = gl_loaders.load_gltf_mesh_arrays(path)
            return _mesh_arrays_tuple(arrays, hidden_submeshes)
    except Exception:
        pass
    try:
        model = gl_loaders.load_model(path)
    except Exception:
        model = None
    if model is None or not getattr(model, "vertices", None):
        return None, None, None
    points = np.asarray(model.vertices, dtype="f4").reshape(-1, 3)
    normals = np.zeros_like(points)
    return points, normals, np.zeros((points.shape[0], 2), dtype="f4")


def _ensure_triangle_arrays(points, normals, uvs):
    if np is None or points is None:
        return None, None, None
    try:
        points = np.asarray(points, dtype="f4").reshape(-1, 3)
    except Exception:
        return None, None, None
    if points.shape[0] < 3:
        return None, None, None
    count = (points.shape[0] // 3) * 3
    points = points[:count]
    try:
        normals = np.asarray(normals, dtype="f4").reshape(-1, 3)[:count]
    except Exception:
        normals = np.zeros_like(points)
    if normals.shape != points.shape or not np.any(np.linalg.norm(normals, axis=1) > 1.0e-6):
        normals = np.zeros_like(points)
        for idx in range(0, count, 3):
            tri = points[idx:idx + 3]
            normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            length = float(np.linalg.norm(normal))
            if length > 1.0e-6:
                normal = normal / length
            normals[idx:idx + 3] = normal
    try:
        uvs = np.asarray(uvs, dtype="f4").reshape(-1, 2)[:count]
    except Exception:
        uvs = np.zeros((count, 2), dtype="f4")
    if uvs.shape[0] != count:
        uvs = np.zeros((count, 2), dtype="f4")
    if not bool(np.any(np.abs(uvs) > 1.0e-8)) and points.size:
        bmin = points.min(axis=0)
        bmax = points.max(axis=0)
        span = np.maximum((bmax - bmin).astype("f4"), np.float32(1.0e-6))
        uvs = np.stack(((points[:, 0] - bmin[0]) / span[0], (points[:, 2] - bmin[2]) / span[2]), axis=1).astype("f4")
    return points.astype("f4", copy=False), normals.astype("f4", copy=False), uvs.astype("f4", copy=False)


def _mask_image_from_item(mask_item):
    model = getattr(mask_item, "model", None)
    provider = getattr(model, "_mask_paint_provider", None) if model is not None else None
    if provider is not None:
        image_fn = getattr(provider, "image", None)
        if callable(image_fn):
            try:
                img = image_fn()
                if img is not None and not img.isNull():
                    return img.convertToFormat(QtGui.QImage.Format_RGBA8888 if hasattr(QtGui.QImage, "Format_RGBA8888") else QtGui.QImage.Format_ARGB32), provider
            except Exception:
                pass
    mask_path = _param_value(model, "mask_path") if model is not None else ""
    if mask_path:
        img = QtGui.QImage(mask_path)
        if not img.isNull():
            return img.convertToFormat(QtGui.QImage.Format_RGBA8888 if hasattr(QtGui.QImage, "Format_RGBA8888") else QtGui.QImage.Format_ARGB32), provider
    return None, provider


def _mask_value(image: QtGui.QImage, u: float, v: float) -> float:
    if image is None or image.isNull():
        return 0.0
    w = max(1, int(image.width()))
    h = max(1, int(image.height()))
    uu_raw = float(u)
    vv_raw = float(v)
    uu = max(0.0, min(1.0, uu_raw)) if -1.0e-5 <= uu_raw <= 1.0 + 1.0e-5 else uu_raw % 1.0
    vv = max(0.0, min(1.0, vv_raw)) if -1.0e-5 <= vv_raw <= 1.0 + 1.0e-5 else vv_raw % 1.0
    x = max(0, min(w - 1, int(round(uu * float(w - 1)))))
    y = max(0, min(h - 1, int(round((1.0 - vv) * float(h - 1)))))
    color = image.pixelColor(x, y)
    return max(0.0, min(1.0, ((float(color.red()) + float(color.green()) + float(color.blue())) / 765.0) * (float(color.alpha()) / 255.0)))


def _mask_luma_array(image: QtGui.QImage):
    if np is None or image is None or image.isNull():
        return None
    try:
        fmt = QtGui.QImage.Format_RGBA8888 if hasattr(QtGui.QImage, "Format_RGBA8888") else QtGui.QImage.Format_ARGB32
        img = image.convertToFormat(fmt)
        w = int(img.width())
        h = int(img.height())
        if w <= 0 or h <= 0:
            return None
        bpl = int(img.bytesPerLine())
        try:
            size = int(img.sizeInBytes())
        except Exception:
            size = int(img.byteCount())
        bits = img.constBits()
        try:
            bits.setsize(size)
        except Exception:
            pass
        arr = np.frombuffer(bits, dtype=np.uint8, count=size)
        arr = arr.reshape((h, bpl))[:, : w * 4].reshape((h, w, 4)).astype("f4")
        rgb = (arr[:, :, 0] + arr[:, :, 1] + arr[:, :, 2]) / (3.0 * 255.0)
        alpha = arr[:, :, 3] / 255.0
        return np.clip(rgb * alpha, 0.0, 1.0).astype("f4", copy=False)
    except Exception:
        try:
            w = int(image.width())
            h = int(image.height())
            values = np.zeros((h, w), dtype="f4")
            for y in range(h):
                for x in range(w):
                    color = image.pixelColor(x, y)
                    values[y, x] = max(
                        0.0,
                        min(
                            1.0,
                            ((float(color.red()) + float(color.green()) + float(color.blue())) / 765.0)
                            * (float(color.alpha()) / 255.0),
                        ),
                    )
            return values
        except Exception:
            return None


def _mask_pixel_candidates(image: QtGui.QImage, threshold: float):
    values = _mask_luma_array(image)
    if values is None or not getattr(values, "size", 0):
        return None, None, None, max(0.0, min(1.0, float(threshold))), 0.0, 0
    max_value = float(np.max(values)) if values.size else 0.0
    effective_threshold = max(0.0, min(1.0, float(threshold)))
    mask = values >= effective_threshold
    if not bool(np.any(mask)) and effective_threshold > 0.05:
        effective_threshold = max(0.05, effective_threshold * 0.35)
        mask = values >= effective_threshold
    if not bool(np.any(mask)):
        return None, None, None, effective_threshold, max_value, 0
    ys, xs = np.nonzero(mask)
    weights = values[ys, xs].astype("f8", copy=False)
    weight_sum = float(np.sum(weights))
    if weight_sum <= 1.0e-12:
        weights = np.ones_like(weights, dtype="f8")
        weight_sum = float(np.sum(weights))
    cdf = np.cumsum(weights / weight_sum)
    cdf[-1] = 1.0
    return xs.astype("i4", copy=False), ys.astype("i4", copy=False), cdf, effective_threshold, max_value, int(xs.shape[0])


def _normal_outward(point, normal, center):
    n = np.asarray(normal, dtype="f4").reshape(3)
    length = float(np.linalg.norm(n))
    if length <= 1.0e-6:
        n = np.asarray(point, dtype="f4").reshape(3) - np.asarray(center, dtype="f4").reshape(3)
        length = float(np.linalg.norm(n))
    if length <= 1.0e-6:
        return np.array([0.0, 1.0, 0.0], dtype="f4")
    n = n / length
    out = np.asarray(point, dtype="f4").reshape(3) - np.asarray(center, dtype="f4").reshape(3)
    if float(np.dot(n, out)) < 0.0:
        n = -n
    return n.astype("f4", copy=False)


def _resolve_mask_input(node_item):
    mask_item = _connected_input_item(node_item, {"mask", "source", "mesh"})
    if mask_item is None:
        return None, "", None, None
    model = getattr(mask_item, "model", None)
    kind = str(getattr(model, "kind", "") or "").strip().lower()
    source_path = _param_value(model, "path") or _param_value(model, "source") or _param_value(model, "mesh")
    image, provider = _mask_image_from_item(mask_item)
    return mask_item, source_path, image, provider


def _source_assets_for_mask(mask_item, source_path: str, provider) -> tuple[dict[str, Any], ...]:
    if mask_item is None:
        return tuple()
    try:
        scene = mask_item.scene()
    except Exception:
        scene = None
    assets: list[dict[str, Any]] = []
    try:
        from nodes.mask import spec as mask_spec  # type: ignore

        resolve_input = getattr(mask_spec, "_resolve_input_item", None)
        src_item = src_kind = src_path = None
        if callable(resolve_input):
            src_item, src_kind, src_path = resolve_input(mask_item)
        src_kind = str(src_kind or "").strip().lower()
        src_path = str(src_path or source_path or "").strip()
        if src_kind == "modeler" and src_item is not None:
            from nodes.modeler import spec as modeler_spec  # type: ignore

            modeler_assets = getattr(modeler_spec, "_modeler_scene_assets", None)
            if callable(modeler_assets):
                assets = [dict(entry) for entry in modeler_assets(scene, src_item) if isinstance(entry, dict)]
        elif src_path:
            ext = Path(src_path).suffix.lower()
            assets = [{"path": src_path, "texture": "", "node": Path(src_path).stem, "ext": ext, "visible": True}]
    except Exception:
        assets = []
    if not assets and source_path:
        ext = Path(source_path).suffix.lower()
        assets = [{"path": source_path, "texture": "", "node": Path(source_path).stem, "ext": ext, "visible": True}]
    for entry in assets:
        if provider is not None:
            entry["texture_provider"] = provider
            entry["texture"] = ""
    return tuple(assets)


def _curves_to_line_points(curves) -> list[list[float]]:
    line_points: list[list[float]] = []
    for curve in curves or []:
        if not isinstance(curve, list) or len(curve) < 2:
            continue
        for idx in range(len(curve) - 1):
            line_points.append(curve[idx])
            line_points.append(curve[idx + 1])
    return line_points


def _uv_triangle_hit(uv, tri_uvs, uv_mins, uv_maxs, uv_denoms):
    if np is None:
        return None, None
    try:
        u = float(uv[0])
        v = float(uv[1])
    except Exception:
        return None, None
    eps = 1.0e-5
    try:
        hits = np.flatnonzero(
            (uv_mins[:, 0] <= u + eps)
            & (uv_maxs[:, 0] >= u - eps)
            & (uv_mins[:, 1] <= v + eps)
            & (uv_maxs[:, 1] >= v - eps)
            & (np.abs(uv_denoms) > 1.0e-10)
        )
    except Exception:
        return None, None
    if not getattr(hits, "size", 0):
        return None, None
    tri = tri_uvs[hits]
    a = tri[:, 0, :]
    b = tri[:, 1, :]
    c = tri[:, 2, :]
    v0 = b - a
    v1 = c - a
    v2 = np.array([u, v], dtype="f4") - a
    denom = uv_denoms[hits]
    b_w = ((v2[:, 0] * v1[:, 1]) - (v1[:, 0] * v2[:, 1])) / denom
    c_w = ((v0[:, 0] * v2[:, 1]) - (v2[:, 0] * v0[:, 1])) / denom
    a_w = 1.0 - b_w - c_w
    inside = (a_w >= -eps) & (b_w >= -eps) & (c_w >= -eps)
    if not bool(np.any(inside)):
        return None, None
    local_indices = np.flatnonzero(inside)
    if local_indices.size > 1:
        scores = np.minimum(np.minimum(a_w[local_indices], b_w[local_indices]), c_w[local_indices])
        local = int(local_indices[int(np.argmax(scores))])
    else:
        local = int(local_indices[0])
    return int(hits[local]), np.array([a_w[local], b_w[local], c_w[local]], dtype="f4")


def _build_guides(points, normals, uvs, mask_img: QtGui.QImage, guide_count: int, points_per_curve: int, threshold: float, length_scale: float, seed: int):
    debug: dict[str, Any] = {
        "requested_guides": int(guide_count),
        "points_per_curve": int(points_per_curve),
        "threshold": float(threshold),
        "length_scale": float(length_scale),
        "seed": int(seed),
    }
    if np is None:
        return None, None, "NumPy is required for Groom Guides.", debug
    points, normals, uvs = _ensure_triangle_arrays(points, normals, uvs)
    if points is None or normals is None or uvs is None:
        return None, None, "Source mesh has no usable triangle data.", debug
    tris = points.reshape(-1, 3, 3)
    tri_norms = normals.reshape(-1, 3, 3)
    tri_uvs = uvs.reshape(-1, 3, 2)
    debug["triangle_count"] = int(tris.shape[0])
    debug["vertex_count"] = int(points.shape[0])
    debug["uv_min"] = [float(v) for v in np.min(uvs, axis=0)]
    debug["uv_max"] = [float(v) for v in np.max(uvs, axis=0)]
    center = (points.min(axis=0) + points.max(axis=0)) * 0.5
    diag = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    guide_length = max(1.0e-4, diag * max(0.001, float(length_scale)))
    debug["guide_length"] = float(guide_length)
    xs, ys, cdf, effective_threshold, mask_max, white_pixels = _mask_pixel_candidates(mask_img, threshold)
    debug["mask_size"] = [int(mask_img.width()), int(mask_img.height())] if mask_img is not None and not mask_img.isNull() else [0, 0]
    debug["mask_max_value"] = float(mask_max)
    debug["mask_effective_threshold"] = float(effective_threshold)
    debug["mask_candidate_pixels"] = int(white_pixels)
    if xs is None or ys is None or cdf is None or int(white_pixels) <= 0:
        return None, None, "No white mask pixels found for guide placement.", debug
    uv_mins = np.min(tri_uvs, axis=1)
    uv_maxs = np.max(tri_uvs, axis=1)
    uv0 = tri_uvs[:, 0, :]
    uv1 = tri_uvs[:, 1, :]
    uv2 = tri_uvs[:, 2, :]
    uv_denoms = ((uv1[:, 0] - uv0[:, 0]) * (uv2[:, 1] - uv0[:, 1])) - ((uv2[:, 0] - uv0[:, 0]) * (uv1[:, 1] - uv0[:, 1]))
    debug["uv_triangle_count"] = int(np.count_nonzero(np.abs(uv_denoms) > 1.0e-10))
    if int(debug["uv_triangle_count"]) <= 0:
        return None, None, "Source mesh has no usable UV triangles for mask projection.", debug
    rng = np.random.default_rng(int(seed))
    max_attempts = max(int(guide_count) * 128, 8192)
    w = max(1, int(mask_img.width()))
    h = max(1, int(mask_img.height()))
    curves: list[list[list[float]]] = []
    attempts = 0
    uv_misses = 0
    while len(curves) < int(guide_count) and attempts < max_attempts:
        attempts += 1
        sample_idx = int(np.searchsorted(cdf, float(rng.random()), side="right"))
        sample_idx = max(0, min(int(xs.shape[0]) - 1, sample_idx))
        u = (float(xs[sample_idx]) + float(rng.random())) / float(w)
        v = 1.0 - ((float(ys[sample_idx]) + float(rng.random())) / float(h))
        tri_idx, bary = _uv_triangle_hit((u, v), tri_uvs, uv_mins, uv_maxs, uv_denoms)
        if tri_idx is None or bary is None:
            uv_misses += 1
            continue
        base = (tris[tri_idx] * bary[:, None]).sum(axis=0)
        normal = (tri_norms[tri_idx] * bary[:, None]).sum(axis=0)
        normal = _normal_outward(base, normal, center)
        curve: list[list[float]] = []
        for idx in range(int(points_per_curve)):
            t = float(idx) / float(max(1, int(points_per_curve) - 1))
            point = base + (normal * guide_length * t)
            curve.append([float(point[0]), float(point[1]), float(point[2])])
        curves.append(curve)
    debug["attempts"] = int(attempts)
    debug["uv_misses"] = int(uv_misses)
    debug["generated_curves"] = int(len(curves))
    if not curves:
        return None, None, "White mask pixels were found, but none landed inside the mesh UV triangles.", debug
    line_points = _curves_to_line_points(curves)
    try:
        curve_points = np.asarray([point for curve in curves for point in curve], dtype="f4").reshape(-1, 3)
        debug["line_segment_count"] = int(len(line_points) // 2)
        debug["curve_bounds_min"] = [float(v) for v in curve_points.min(axis=0)]
        debug["curve_bounds_max"] = [float(v) for v in curve_points.max(axis=0)]
    except Exception:
        debug["line_segment_count"] = int(len(line_points) // 2)
    detail = f"Generated {len(curves)} guide curve(s)."
    if len(curves) < int(guide_count):
        detail = f"Generated {len(curves)} guide curve(s); paint more white mask area for the requested count."
    elif float(effective_threshold) < float(threshold):
        detail = f"Generated {len(curves)} guide curve(s) from soft mask edges."
    return curves, line_points, detail, debug


def build_groom_guides_scene_asset(node_item) -> GroomGuidesBuildOutcome:
    debug: dict[str, Any] = {}
    if np is None:
        return GroomGuidesBuildOutcome(None, tuple(), "error", "NumPy is required for Groom Guides.", debug)
    model = getattr(node_item, "model", None)
    mask_item, source_path, mask_img, provider = _resolve_mask_input(node_item)
    source_path = str(source_path or "").strip()
    debug["mask_node"] = str(getattr(getattr(mask_item, "model", None), "name", "") or "") if mask_item is not None else ""
    debug["source_path"] = source_path
    if mask_item is None:
        return GroomGuidesBuildOutcome(None, tuple(), "error", "Connect a Mask node.", debug)
    if not source_path:
        return GroomGuidesBuildOutcome(None, tuple(), "error", "The connected Mask node has no source mesh.", debug)
    try:
        source_file = Path(source_path)
    except Exception:
        return GroomGuidesBuildOutcome(None, tuple(), "error", "Source mesh path is invalid.", debug)
    if not source_file.exists() or source_file.suffix.lower() not in SUPPORTED_MESH_EXTS:
        debug["source_exists"] = bool(source_file.exists())
        debug["source_ext"] = source_file.suffix.lower()
        return GroomGuidesBuildOutcome(None, tuple(), "error", "Source mesh file was not found or is unsupported.", debug)
    if mask_img is None or mask_img.isNull():
        return GroomGuidesBuildOutcome(None, tuple(), "error", "The connected Mask node has no mask image.", debug)
    source_assets = _source_assets_for_mask(mask_item, source_path, provider)
    hidden_submeshes: list[str] = []
    for entry in source_assets:
        if not isinstance(entry, dict):
            continue
        hidden_submeshes.extend([str(name).strip() for name in (entry.get("hidden_submeshes") or []) if str(name).strip()])
    points, normals, uvs = _load_mesh_arrays(source_file, hidden_submeshes)
    if points is None:
        return GroomGuidesBuildOutcome(None, source_assets, "error", "Could not read the source mesh.", debug)
    try:
        guide_count = max(1, min(10000, int(float(_param_value(model, "guide_count", "256")))))
    except Exception:
        guide_count = 256
    try:
        points_per_curve = max(2, min(GROOM_GUIDES_SEGMENTS_HARD_MAX, int(float(_param_value(model, "segments", "5")))))
    except Exception:
        points_per_curve = 5
    try:
        threshold = max(0.0, min(1.0, float(_param_value(model, "threshold", "0.35"))))
    except Exception:
        threshold = 0.35
    try:
        length_scale = max(0.001, min(2.0, float(_param_value(model, "length", "0.08"))))
    except Exception:
        length_scale = 0.08
    try:
        seed = int(float(_param_value(model, "seed", "7")))
    except Exception:
        seed = 7
    curves, line_points, detail, build_debug = _build_guides(points, normals, uvs, mask_img, guide_count, points_per_curve, threshold, length_scale, seed)
    if isinstance(build_debug, dict):
        debug.update(build_debug)
    if not curves or not line_points:
        return GroomGuidesBuildOutcome(None, source_assets, "error", detail, debug)
    root_indices = [int(idx * points_per_curve) for idx in range(len(curves))]
    point_groups = {"root": list(root_indices)}
    source_owner = ""
    try:
        for entry in source_assets:
            if not isinstance(entry, dict):
                continue
            source_owner = str(entry.get("node") or "").strip()
            if source_owner:
                break
    except Exception:
        source_owner = ""
    if not source_owner:
        source_owner = Path(source_path).stem
    debug["source_owner"] = source_owner
    mask_path = _param_value(getattr(mask_item, "model", None), "mask_path")
    output_path = _output_path(node_item, source_path, mask_path, guide_count, points_per_curve, threshold, length_scale, seed)
    payload = {
        "schema": "qubit.groom_guides.v1",
        "source_path": source_path,
        "mask_path": mask_path,
        "guide_count": len(curves),
        "points_per_curve": int(points_per_curve),
        "hidden_submeshes": list(hidden_submeshes),
        "length": float(length_scale),
        "root_indices": list(root_indices),
        "point_groups": dict(point_groups),
        "debug": dict(debug),
        "curves": curves,
    }
    try:
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass
    _set_param(node_item, "source", source_path, notify_scene=False)
    _set_param(node_item, "path", str(output_path), notify_scene=False)
    _set_param(node_item, "guides_path", str(output_path), notify_scene=False)
    asset = {
        "kind": "groom_guides",
        "node": str(getattr(model, "name", "") or "groom_guides"),
        "visible": True,
        "guides_path": str(output_path),
        "source_path": source_path,
        "source_owner": source_owner,
        "guide_count": len(curves),
        "points_per_curve": int(points_per_curve),
        "length": float(length_scale),
        "hidden_submeshes": list(hidden_submeshes),
        "root_indices": list(root_indices),
        "point_groups": dict(point_groups),
        "debug": dict(debug),
        "curves": curves,
        "line_points": line_points,
    }
    debug["guides_path"] = str(output_path)
    return GroomGuidesBuildOutcome(asset, source_assets, "ok", detail, debug)


def format_groom_guides_debug_report(outcome: GroomGuidesBuildOutcome) -> str:
    debug = dict(getattr(outcome, "debug", None) or {})
    lines = [
        f"timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"status: {str(getattr(outcome, 'status', '') or '')}",
        f"detail: {str(getattr(outcome, 'detail', '') or '')}",
    ]
    for key in GROOM_GUIDES_DEBUG_KEYS:
        if key in debug:
            lines.append(f"{key}: {debug.get(key)}")
    lines.append("")
    lines.append("debug_json:")
    try:
        lines.append(json.dumps(debug, indent=2, sort_keys=True, default=str))
    except Exception:
        lines.append(str(debug))
    return "\n".join(lines)


def write_groom_guides_debug_report(report: str) -> Path:
    log_dir = _logs_dir()
    latest_path = log_dir / "groom_guides_debug_latest.txt"
    append_path = log_dir / "groom_guides_debug.log"
    latest_path.write_text(str(report or ""), encoding="utf-8")
    with append_path.open("a", encoding="utf-8") as handle:
        handle.write(str(report or ""))
        handle.write("\n\n")
    return latest_path


def show_groom_guides_debug_report(node_item, parent=None) -> bool:
    outcome = build_groom_guides_scene_asset(node_item)
    report = format_groom_guides_debug_report(outcome)
    try:
        log_path = write_groom_guides_debug_report(report)
    except Exception:
        log_path = None

    dialog = QtWidgets.QDialog(parent or _resolve_window(node_item))
    dialog.setWindowTitle("Groom Guides Debug")
    dialog.resize(720, 520)
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(8)

    if log_path is not None:
        label = QtWidgets.QLabel(f"Saved to: {log_path}")
    else:
        label = QtWidgets.QLabel("Could not write debug report to logs folder.")
    label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard)
    layout.addWidget(label)

    text = QtWidgets.QTextEdit()
    text.setReadOnly(True)
    text.setAcceptRichText(False)
    text.setPlainText(report)
    text.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
    layout.addWidget(text, 1)

    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    copy_btn = QtWidgets.QPushButton("Copy")
    close_btn = QtWidgets.QPushButton("Close")

    def _copy_report():
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(report)
        copy_btn.setText("Copied")

    copy_btn.clicked.connect(_copy_report)
    close_btn.clicked.connect(dialog.accept)
    row.addWidget(copy_btn)
    row.addWidget(close_btn)
    layout.addLayout(row)
    dialog.exec()
    return True


class GroomGuidesWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending_update = False
        self._last_outcome: Optional[GroomGuidesBuildOutcome] = None
        self._ensure_defaults()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        self._count_slider, self._count_value = self._make_slider(8, 2000, self._int_param("guide_count", 256))
        layout.addLayout(self._slider_row("Guides", self._count_slider, self._count_value))
        segments_value = max(2, min(GROOM_GUIDES_SEGMENTS_HARD_MAX, self._int_param("segments", 5)))
        self._segments_slider, self._segments_value = self._make_slider(
            2,
            max(16, segments_value),
            segments_value,
            editable=True,
            hard_max=GROOM_GUIDES_SEGMENTS_HARD_MAX,
        )
        layout.addLayout(self._slider_row("Segments", self._segments_slider, self._segments_value))
        length_slider_value = int(round(max(0.001, min(2.0, self._float_param("length", 0.08))) * 1000.0))
        self._length_slider, self._length_value = self._make_slider(1, 2000, length_slider_value)
        self._length_value.setText(self._format_length(self._length_slider.value()))
        layout.addLayout(self._slider_row("Length", self._length_slider, self._length_value))

        self._status = QtWidgets.QLabel("Generated guide curves: Connect Mask")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(30)
        self._status.setStyleSheet("QLabel{color:#cbd5e1;font-size:10px;}")
        layout.addWidget(self._status, 0)

        view_row = QtWidgets.QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(6)
        view_row.addStretch(1)
        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(72)
        self._view_btn.clicked.connect(self._on_view_clicked)
        view_row.addWidget(self._view_btn, 0)
        layout.addLayout(view_row)

        self._count_slider.valueChanged.connect(self._on_count_changed)
        self._segments_slider.valueChanged.connect(self._on_segments_changed)
        self._segments_value.valueChanged.connect(self._on_segments_entered)
        self._length_slider.valueChanged.connect(self._on_length_changed)

        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._update_state)

    def sizeHint(self):
        return QtCore.QSize(GROOM_GUIDES_NODE_W, GROOM_GUIDES_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(GROOM_GUIDES_NODE_W, GROOM_GUIDES_BODY_H)

    def _int_param(self, name: str, default: int) -> int:
        model = getattr(self._node_item, "model", None)
        try:
            return int(float(_param_value(model, name, str(default))))
        except Exception:
            return int(default)

    def _float_param(self, name: str, default: float) -> float:
        model = getattr(self._node_item, "model", None)
        try:
            return float(_param_value(model, name, str(default)))
        except Exception:
            return float(default)

    def _ensure_defaults(self) -> None:
        for name, default in (
            ("mask", ""),
            ("source", ""),
            ("path", ""),
            ("guides_path", ""),
            ("guide_count", "256"),
            ("segments", "5"),
            ("threshold", "0.35"),
            ("length", "0.08"),
            ("seed", "7"),
            ("debug_log", "0"),
        ):
            _ensure_param(self._node_item, name, default)
        _ensure_hidden_params(getattr(self._node_item, "model", None), GROOM_GUIDES_HIDDEN_PARAMS)
        if hasattr(self._node_item, "ensure_input"):
            self._node_item.ensure_input("mask")

    def _make_slider(self, minimum: int, maximum: int, value: int, *, editable: bool = False, hard_max: Optional[int] = None):
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        max_value = max(int(maximum), int(value))
        slider.setRange(int(minimum), int(max_value))
        slider.setValue(max(int(minimum), min(int(max_value), int(value))))
        slider.setMinimumWidth(110)
        if editable:
            value_widget = QtWidgets.QSpinBox()
            value_widget.setRange(int(minimum), int(hard_max or max_value))
            value_widget.setValue(int(slider.value()))
            value_widget.setKeyboardTracking(False)
            value_widget.setFixedWidth(58)
            value_widget.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            return slider, value_widget
        label = QtWidgets.QLabel(str(slider.value()))
        label.setFixedWidth(50)
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        return slider, label

    @staticmethod
    def _format_length(slider_value: int) -> str:
        return f"{max(1, int(slider_value)) / 1000.0:.3f}"

    def _slider_row(self, text: str, slider: QtWidgets.QSlider, value_label: QtWidgets.QWidget):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QtWidgets.QLabel(text)
        label.setFixedWidth(58)
        row.addWidget(label, 0)
        row.addWidget(slider, 1)
        row.addWidget(value_label, 0)
        return row

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        for signal_name in ("linksChanged", "paramChanged"):
            signal = getattr(self._scene, signal_name, None)
            if signal is not None:
                try:
                    signal.connect(self._schedule_update)
                except Exception:
                    pass
        self._scene_connected = True

    def _schedule_update(self, *_args):
        if self._pending_update:
            return
        self._pending_update = True
        QtCore.QTimer.singleShot(80, self._update_state)

    def _update_state(self):
        self._pending_update = False
        outcome = build_groom_guides_scene_asset(self._node_item)
        self._last_outcome = outcome
        ok = bool(outcome.asset and outcome.status == "ok")
        self._view_btn.setEnabled(ok)
        self._view_btn.setToolTip(outcome.detail)
        if ok:
            count = 0
            try:
                count = int((outcome.debug or {}).get("generated_curves", 0) or 0)
            except Exception:
                count = 0
            self._status.setText(f"Generated guide curves: {count}")
        else:
            self._status.setText(f"Generated guide curves: {outcome.detail}")

    def _on_count_changed(self, value: int):
        self._count_value.setText(str(int(value)))
        _set_param(self._node_item, "guide_count", str(int(value)), notify_scene=True)
        self._schedule_update()

    def _on_segments_changed(self, value: int):
        try:
            old = self._segments_value.blockSignals(True)
            self._segments_value.setValue(int(value))
            self._segments_value.blockSignals(old)
        except Exception:
            try:
                self._segments_value.setText(str(int(value)))
            except Exception:
                pass
        _set_param(self._node_item, "segments", str(int(value)), notify_scene=True)
        self._schedule_update()

    def _on_segments_entered(self, value: int):
        value = max(2, min(GROOM_GUIDES_SEGMENTS_HARD_MAX, int(value)))
        if value > int(self._segments_slider.maximum()):
            self._segments_slider.setMaximum(value)
        try:
            old = self._segments_slider.blockSignals(True)
            self._segments_slider.setValue(value)
            self._segments_slider.blockSignals(old)
        except Exception:
            self._segments_slider.setValue(value)
        _set_param(self._node_item, "segments", str(value), notify_scene=True)
        self._schedule_update()

    def _on_length_changed(self, value: int):
        text = self._format_length(int(value))
        self._length_value.setText(text)
        _set_param(self._node_item, "length", text, notify_scene=True)
        self._schedule_update()

    def _on_view_clicked(self):
        outcome = build_groom_guides_scene_asset(self._node_item)
        self._last_outcome = outcome
        if not outcome.asset:
            QtWidgets.QMessageBox.warning(_resolve_window(self._node_item) or self, "Groom Guides", outcome.detail)
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(handler):
            assets = [dict(entry) for entry in outcome.source_assets] + [dict(outcome.asset)]
            try:
                handler(assets, frame=True)
            except TypeError:
                handler(assets)


def build_ports(node_item) -> None:
    for name, default in (
        ("mask", ""),
        ("source", ""),
        ("path", ""),
        ("guides_path", ""),
        ("guide_count", "256"),
        ("segments", "5"),
        ("threshold", "0.35"),
        ("length", "0.08"),
        ("seed", "7"),
        ("debug_log", "0"),
    ):
        _ensure_param(node_item, name, default)
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mask")
    _ensure_hidden_params(getattr(node_item, "model", None), GROOM_GUIDES_HIDDEN_PARAMS)


def render_node_body(node_item, y_cursor: int) -> int:
    body = GroomGuidesWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    h = max(int(body.sizeHint().height()), int(body.minimumSizeHint().height()))
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


GROOM_GUIDES_SPEC = Spec(
    stripe_color="#f59e0b",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "GROOM_GUIDES_SPEC",
    "GroomGuidesBuildOutcome",
    "build_groom_guides_scene_asset",
    "format_groom_guides_debug_report",
    "show_groom_guides_debug_report",
    "write_groom_guides_debug_report",
]
