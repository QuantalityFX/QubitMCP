from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Tuple
import math

from .fbx_canonical import (
    AnimationClip,
    JointAnimationTrack,
    QuatKeyframe,
    SkeletonAsset,
    Vec3Keyframe,
)

_TIME_EPSILON = 1e-8
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

_ANIMATION_INGEST_CACHE_MAX = 8
_ANIMATION_INGEST_CACHE: "OrderedDict[Tuple[str, int, int, Tuple[str, ...]], FBXAnimationIngestResult]" = OrderedDict()


class FBXAnimationIngestError(RuntimeError):
    """Raised when deterministic FBX animation ingest fails."""


@dataclass(eq=True)
class FBXAnimationIngestResult:
    source_path: str
    clips: List[AnimationClip] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _safe_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


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


def _matrix4_transpose(matrix16: Tuple[float, ...]) -> Tuple[float, ...]:
    values = tuple(matrix16 or ())
    if len(values) != 16:
        return _IDENTITY_MATRIX_4X4
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[(r * 4) + c] = _to_float(values[(c * 4) + r], 0.0)
    return tuple(out)


def _matrix4_translation_channels(matrix16: Tuple[float, ...]) -> Tuple[float, float]:
    values = tuple(matrix16 or ())
    if len(values) != 16:
        return 0.0, 0.0
    col = (
        abs(_to_float(values[3], 0.0))
        + abs(_to_float(values[7], 0.0))
        + abs(_to_float(values[11], 0.0))
    )
    row = (
        abs(_to_float(values[12], 0.0))
        + abs(_to_float(values[13], 0.0))
        + abs(_to_float(values[14], 0.0))
    )
    return float(col), float(row)


def _matrix4_to_canonical(matrix16: Tuple[float, ...]) -> Tuple[float, ...]:
    col_mag, row_mag = _matrix4_translation_channels(matrix16)
    if row_mag > max(1.0e-5, col_mag * 4.0):
        return _matrix4_transpose(matrix16)
    return tuple(matrix16 or _IDENTITY_MATRIX_4X4)


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


def _decompose_local_trs(matrix16: Tuple[float, ...]) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]:
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
    return (m03, m13, m23), (qx, qy, qz, qw), (sx, sy, sz)


def _dedupe_times(times: Sequence[float]) -> List[float]:
    values = sorted(float(t) for t in times)
    out: List[float] = []
    for value in values:
        if out and abs(value - out[-1]) <= _TIME_EPSILON:
            continue
        out.append(value)
    return out


def _derive_sample_rate_hz(times: Sequence[float], fallback: float) -> float:
    unique = _dedupe_times(times)
    min_delta: float | None = None
    for i in range(1, len(unique)):
        dt = float(unique[i]) - float(unique[i - 1])
        if dt <= _TIME_EPSILON:
            continue
        if min_delta is None or dt < min_delta:
            min_delta = dt
    if min_delta is not None and min_delta > _TIME_EPSILON:
        return max(1.0, 1.0 / min_delta)
    return max(1.0, float(fallback))


def _extract_key_time_value(raw_key: Any) -> Tuple[float, Any]:
    if hasattr(raw_key, "time") and hasattr(raw_key, "value"):
        return _to_float(getattr(raw_key, "time"), 0.0), getattr(raw_key, "value")
    if hasattr(raw_key, "mTime") and hasattr(raw_key, "mValue"):
        return _to_float(getattr(raw_key, "mTime"), 0.0), getattr(raw_key, "mValue")
    if isinstance(raw_key, dict):
        if "time" in raw_key and "value" in raw_key:
            return _to_float(raw_key["time"], 0.0), raw_key["value"]
        if "mTime" in raw_key and "mValue" in raw_key:
            return _to_float(raw_key["mTime"], 0.0), raw_key["mValue"]
    if isinstance(raw_key, Sequence) and not isinstance(raw_key, (str, bytes)):
        if len(raw_key) >= 2:
            return _to_float(raw_key[0], 0.0), raw_key[1]
    return 0.0, None


def _vec3_from_any(value: Any) -> Tuple[float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0)
    if isinstance(value, dict):
        if all(axis in value for axis in ("x", "y", "z")):
            return (
                _to_float(value["x"], 0.0),
                _to_float(value["y"], 0.0),
                _to_float(value["z"], 0.0),
            )
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):
        return (
            _to_float(getattr(value, "x"), 0.0),
            _to_float(getattr(value, "y"), 0.0),
            _to_float(getattr(value, "z"), 0.0),
        )
    if hasattr(value, "mData"):
        data = getattr(value, "mData")
        try:
            return (
                _to_float(data[0], 0.0),
                _to_float(data[1], 0.0),
                _to_float(data[2], 0.0),
            )
        except Exception:
            pass
    try:
        return (
            _to_float(value[0], 0.0),  # type: ignore[index]
            _to_float(value[1], 0.0),  # type: ignore[index]
            _to_float(value[2], 0.0),  # type: ignore[index]
        )
    except Exception:
        pass
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) >= 3:
            return (
                _to_float(value[0], 0.0),
                _to_float(value[1], 0.0),
                _to_float(value[2], 0.0),
            )
    return (0.0, 0.0, 0.0)


def _quat_from_any(value: Any) -> Tuple[float, float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0, 1.0)
    if isinstance(value, dict):
        if all(axis in value for axis in ("x", "y", "z", "w")):
            return (
                _to_float(value["x"], 0.0),
                _to_float(value["y"], 0.0),
                _to_float(value["z"], 0.0),
                _to_float(value["w"], 1.0),
            )
    if all(hasattr(value, axis) for axis in ("x", "y", "z", "w")):
        return (
            _to_float(getattr(value, "x"), 0.0),
            _to_float(getattr(value, "y"), 0.0),
            _to_float(getattr(value, "z"), 0.0),
            _to_float(getattr(value, "w"), 1.0),
        )
    try:
        return (
            _to_float(value[0], 0.0),  # type: ignore[index]
            _to_float(value[1], 0.0),  # type: ignore[index]
            _to_float(value[2], 0.0),  # type: ignore[index]
            _to_float(value[3], 1.0),  # type: ignore[index]
        )
    except Exception:
        pass
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) >= 4:
            return (
                _to_float(value[0], 0.0),
                _to_float(value[1], 0.0),
                _to_float(value[2], 0.0),
                _to_float(value[3], 1.0),
            )
    return (0.0, 0.0, 0.0, 1.0)


def _sorted_unique_vec3_keys(raw_keys: Sequence[Any], ticks_per_second: float) -> List[Vec3Keyframe]:
    indexed: List[Tuple[float, int, Tuple[float, float, float]]] = []
    scale = ticks_per_second if ticks_per_second > _TIME_EPSILON else 1.0
    for index, raw in enumerate(list(raw_keys or [])):
        raw_time, raw_value = _extract_key_time_value(raw)
        t = _to_float(raw_time, 0.0) / scale
        indexed.append((t, index, _vec3_from_any(raw_value)))
    indexed.sort(key=lambda item: (item[0], item[1]))

    deduped: List[Tuple[float, Tuple[float, float, float]]] = []
    for t, _index, value in indexed:
        if deduped and abs(t - deduped[-1][0]) <= _TIME_EPSILON:
            deduped[-1] = (t, value)
        else:
            deduped.append((t, value))
    return [Vec3Keyframe(time=t, value=value, interpolation="linear") for t, value in deduped]


def _sorted_unique_quat_keys(raw_keys: Sequence[Any], ticks_per_second: float) -> List[QuatKeyframe]:
    indexed: List[Tuple[float, int, Tuple[float, float, float, float]]] = []
    scale = ticks_per_second if ticks_per_second > _TIME_EPSILON else 1.0
    for index, raw in enumerate(list(raw_keys or [])):
        raw_time, raw_value = _extract_key_time_value(raw)
        t = _to_float(raw_time, 0.0) / scale
        indexed.append((t, index, _quat_from_any(raw_value)))
    indexed.sort(key=lambda item: (item[0], item[1]))

    deduped: List[Tuple[float, Tuple[float, float, float, float]]] = []
    for t, _index, value in indexed:
        if deduped and abs(t - deduped[-1][0]) <= _TIME_EPSILON:
            deduped[-1] = (t, value)
        else:
            deduped.append((t, value))
    return [QuatKeyframe(time=t, value=value, interpolation="linear") for t, value in deduped]


def _channel_node_name(channel_obj: Any, fallback: str) -> str:
    for key in ("node_name", "nodename", "nodeName"):
        value = getattr(channel_obj, key, None)
        text = str(value or "").strip()
        if text:
            return text
    node_obj = getattr(channel_obj, "node", None)
    if node_obj is not None:
        text = str(getattr(node_obj, "name", "") or "").strip()
        if text:
            return text
    return fallback


def _channel_keys(channel_obj: Any, keys: Sequence[str]) -> List[Any]:
    for key in keys:
        value = getattr(channel_obj, key, None)
        if value is not None:
            try:
                return list(value)
            except Exception:
                return []
    return []


def _build_clip_from_pyassimp_animation(
    anim_obj: Any,
    clip_index: int,
    *,
    skeleton_names: set[str] | None,
    warnings: List[str],
) -> AnimationClip | None:
    ticks_per_second = _to_float(getattr(anim_obj, "tickspersecond", 0.0), 0.0)
    duration_ticks = _to_float(getattr(anim_obj, "duration", 0.0), 0.0)
    if ticks_per_second <= _TIME_EPSILON:
        ticks_per_second = 1.0

    channels = list(getattr(anim_obj, "channels", None) or getattr(anim_obj, "nodechannels", None) or [])
    indexed_channels = list(enumerate(channels))
    indexed_channels.sort(
        key=lambda item: (
            _channel_node_name(item[1], f"joint_{item[0]}").lower(),
            item[0],
        )
    )

    tracks: List[JointAnimationTrack] = []
    all_times: List[float] = []
    filtered_count = 0
    for index, channel in indexed_channels:
        joint_name = _safe_text(_channel_node_name(channel, f"joint_{index}"), f"joint_{index}")

        translation_keys = _sorted_unique_vec3_keys(
            _channel_keys(channel, ("positionkeys", "position_keys", "translation_keys")),
            ticks_per_second,
        )
        rotation_keys = _sorted_unique_quat_keys(
            _channel_keys(channel, ("rotationkeys", "rotation_keys")),
            ticks_per_second,
        )
        scale_keys = _sorted_unique_vec3_keys(
            _channel_keys(channel, ("scalingkeys", "scaling_keys", "scale_keys")),
            ticks_per_second,
        )
        if not (translation_keys or rotation_keys or scale_keys):
            continue

        if skeleton_names is not None and joint_name not in skeleton_names:
            filtered_count += 1
            continue

        tracks.append(
            JointAnimationTrack(
                joint_name=joint_name,
                translation_keys=translation_keys,
                rotation_keys=rotation_keys,
                scale_keys=scale_keys,
            )
        )
        all_times.extend(key.time for key in translation_keys)
        all_times.extend(key.time for key in rotation_keys)
        all_times.extend(key.time for key in scale_keys)

    if filtered_count > 0:
        warnings.append(
            f"clip_{clip_index}: {filtered_count} track(s) were ignored because they are not part of the active skeleton."
        )

    if not tracks:
        return None

    clip_name = _safe_text(getattr(anim_obj, "name", ""), f"clip_{clip_index}")
    min_time = min(all_times) if all_times else 0.0
    max_time = max(all_times) if all_times else min_time
    duration_sec = duration_ticks / ticks_per_second if duration_ticks > 0.0 else 0.0
    end_time = max(max_time, min_time + max(0.0, duration_sec))
    if ticks_per_second > 1.0:
        sample_rate = float(ticks_per_second)
    else:
        sample_rate = _derive_sample_rate_hz(all_times, fallback=30.0)

    clip = AnimationClip(
        name=clip_name,
        start_time=min_time,
        end_time=end_time,
        sample_rate_hz=sample_rate,
        tracks=tracks,
        metadata={
            "source_backend": "pyassimp",
            "source_clip_index": int(clip_index),
        },
    )
    return clip


def _ingest_pyassimp_scene(
    *,
    path_obj: Path,
    scene_obj: Any,
    skeleton: SkeletonAsset | None,
) -> FBXAnimationIngestResult:
    warnings: List[str] = []
    animations = list(getattr(scene_obj, "animations", None) or [])
    indexed = list(enumerate(animations))
    indexed.sort(
        key=lambda item: (
            _safe_text(getattr(item[1], "name", ""), f"clip_{item[0]}").lower(),
            item[0],
        )
    )

    skeleton_names = set(skeleton.joint_names) if skeleton is not None else None
    clips: List[AnimationClip] = []
    for clip_index, anim in indexed:
        clip = _build_clip_from_pyassimp_animation(
            anim,
            clip_index,
            skeleton_names=skeleton_names,
            warnings=warnings,
        )
        if clip is None:
            continue
        clip.validate(skeleton=skeleton)
        clips.append(clip)

    if not clips:
        warnings.append("No animation clips were found in source.")
    return FBXAnimationIngestResult(
        source_path=str(path_obj),
        clips=clips,
        warnings=warnings,
    )


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
def _load_fbxsdk_scene(path_obj: Path) -> Iterator[Tuple[Any, Any]]:
    try:
        import fbx  # type: ignore
    except Exception as exc:
        raise FBXAnimationIngestError(f"fbx sdk unavailable: {exc}") from exc

    manager = None
    importer = None
    try:
        manager = fbx.FbxManager.Create()
        if manager is None:
            raise FBXAnimationIngestError("fbx sdk manager creation failed.")
        ios = fbx.FbxIOSettings.Create(manager, getattr(fbx, "IOSROOT", ""))
        if ios is not None:
            manager.SetIOSettings(ios)

        importer = fbx.FbxImporter.Create(manager, "")
        if importer is None:
            raise FBXAnimationIngestError("fbx sdk importer creation failed.")
        try:
            ok = importer.Initialize(str(path_obj), -1, manager.GetIOSettings())
        except Exception:
            ok = importer.Initialize(str(path_obj), -1)
        if not ok:
            raise FBXAnimationIngestError(
                f"Failed to initialize FBX SDK importer: {_fbxsdk_status_error_text(importer)}"
            )

        scene = fbx.FbxScene.Create(manager, path_obj.stem or "scene")
        if scene is None:
            raise FBXAnimationIngestError("fbx sdk scene creation failed.")
        if not importer.Import(scene):
            raise FBXAnimationIngestError(
                f"Failed to import FBX with FBX SDK: {_fbxsdk_status_error_text(importer)}"
            )
        yield fbx, scene
    except FBXAnimationIngestError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise FBXAnimationIngestError(f"Failed to load FBX via fbx sdk: {exc}") from exc
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
        raise FBXAnimationIngestError(f"pyassimp unavailable: {exc}") from exc

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
    raise FBXAnimationIngestError(f"Failed to load FBX via pyassimp: {summary}")


def _fbxsdk_matrix4_tuple(mat_obj: Any) -> Tuple[float, ...]:
    if mat_obj is None:
        return _IDENTITY_MATRIX_4X4
    try:
        matrix = tuple(_to_float(mat_obj.Get(r, c), 0.0) for r in range(4) for c in range(4))
        return _matrix4_to_canonical(matrix)
    except Exception:
        pass
    return _matrix4_to_canonical(_matrix4_from_obj(mat_obj))


def _fbxsdk_curve_components(fbx_mod: Any) -> List[str]:
    out: List[str] = []
    for key in (
        "FBXSDK_CURVENODE_COMPONENT_X",
        "FBXSDK_CURVENODE_COMPONENT_Y",
        "FBXSDK_CURVENODE_COMPONENT_Z",
    ):
        value = getattr(fbx_mod, key, None)
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    for fallback in ("X", "Y", "Z", "d|X", "d|Y", "d|Z"):
        if fallback not in out:
            out.append(fallback)
    return out


def _fbxsdk_property_curve_times(prop_obj: Any, layers: Sequence[Any], components: Sequence[str]) -> List[float]:
    out: List[float] = []
    seen_curves: set[int] = set()
    if prop_obj is None:
        return out
    for layer in list(layers or []):
        for component in list(components or []):
            curve = None
            try:
                curve = prop_obj.GetCurve(layer, component)
            except Exception:
                curve = None
            if curve is None:
                continue
            curve_id = id(curve)
            if curve_id in seen_curves:
                continue
            seen_curves.add(curve_id)
            count = _to_int(getattr(curve, "KeyGetCount", lambda: 0)(), 0)
            for idx in range(max(0, count)):
                try:
                    time_obj = curve.KeyGetTime(idx)
                    out.append(_to_float(time_obj.GetSecondDouble(), 0.0))
                except Exception:
                    continue
    return _dedupe_times(out)


def _fbxsdk_sorted_children(node_obj: Any) -> List[Any]:
    count = _to_int(getattr(node_obj, "GetChildCount", lambda: 0)(), 0)
    indexed: List[Tuple[int, Any]] = []
    for i in range(max(0, count)):
        try:
            child = node_obj.GetChild(i)
        except Exception:
            child = None
        if child is not None:
            indexed.append((i, child))
    indexed.sort(
        key=lambda item: (
            _safe_text(getattr(item[1], "GetName", lambda: "")(), f"child_{item[0]}").lower(),
            item[0],
        )
    )
    return [child for _, child in indexed]


def _fbxsdk_collect_nodes(root_node: Any) -> List[Any]:
    out: List[Any] = []

    def _visit(node_obj: Any) -> None:
        if node_obj is None:
            return
        out.append(node_obj)
        for child in _fbxsdk_sorted_children(node_obj):
            _visit(child)

    _visit(root_node)
    return out


def _fbxsdk_list_animation_stacks(fbx_mod: Any, scene_obj: Any) -> List[Any]:
    indexed: List[Tuple[int, Any]] = []
    try:
        criteria = fbx_mod.FbxCriteria.ObjectType(fbx_mod.FbxAnimStack.ClassId)
        count = _to_int(scene_obj.GetSrcObjectCount(criteria), 0)
        for i in range(max(0, count)):
            try:
                stack = scene_obj.GetSrcObject(criteria, i)
            except Exception:
                stack = None
            if stack is not None:
                indexed.append((i, stack))
    except Exception:
        pass
    if not indexed:
        try:
            current = scene_obj.GetCurrentAnimationStack()
        except Exception:
            current = None
        if current is not None:
            indexed.append((0, current))

    dedup: List[Tuple[int, Any]] = []
    seen: set[int] = set()
    for idx, stack in indexed:
        token = id(stack)
        if token in seen:
            continue
        seen.add(token)
        dedup.append((idx, stack))
    dedup.sort(
        key=lambda item: (
            _safe_text(getattr(item[1], "GetName", lambda: "")(), f"clip_{item[0]}").lower(),
            item[0],
        )
    )
    return [stack for _, stack in dedup]


def _fbxsdk_list_anim_layers(fbx_mod: Any, stack_obj: Any) -> List[Any]:
    layers: List[Any] = []
    try:
        layer_class = fbx_mod.FbxAnimLayer.ClassId
        count = _to_int(stack_obj.GetMemberCount(layer_class), 0)
        for i in range(max(0, count)):
            try:
                layer = stack_obj.GetMember(layer_class, i)
            except Exception:
                layer = None
            if layer is not None:
                layers.append(layer)
    except Exception:
        pass
    return layers


def _fbxsdk_stack_time_span_seconds(fbx_mod: Any, stack_obj: Any) -> Tuple[float, float] | None:
    span_obj = None
    try:
        span_obj = fbx_mod.FbxTimeSpan()
        ok = bool(stack_obj.GetLocalTimeSpan(span_obj))
    except Exception:
        ok = False
    if not ok or span_obj is None:
        return None
    try:
        start = _to_float(span_obj.GetStart().GetSecondDouble(), 0.0)
        end = _to_float(span_obj.GetStop().GetSecondDouble(), start)
        if end < start:
            end = start
        return start, end
    except Exception:
        return None


def _fbxsdk_scene_sample_rate(scene_obj: Any, fbx_mod: Any, fallback: float) -> float:
    try:
        settings = scene_obj.GetGlobalSettings()
        mode = settings.GetTimeMode()
        frame_rate = _to_float(fbx_mod.FbxTime.GetFrameRate(mode), 0.0)
        if frame_rate > 0.0:
            return frame_rate
    except Exception:
        pass
    return max(1.0, float(fallback))


def _fbxsdk_eval_local_trs(node_obj: Any, t_seconds: float, fbx_mod: Any) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]:
    try:
        time_obj = fbx_mod.FbxTime()
        time_obj.SetSecondDouble(float(t_seconds))
        mat = node_obj.EvaluateLocalTransform(time_obj)
    except Exception:
        mat = None
    return _fbxsdk_local_trs_from_matrix(mat)


def _fbxsdk_local_trs_from_matrix(
    mat_obj: Any,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]:
    if mat_obj is not None:
        try:
            t = _vec3_from_any(mat_obj.GetT())
            q = _quat_from_any(mat_obj.GetQ())
            s = _vec3_from_any(mat_obj.GetS())
            # FBX SDK bindings can expose vector/quaternion wrappers that parse as zeros;
            # fall back to matrix decomposition when scale is invalid.
            if (abs(s[0]) + abs(s[1]) + abs(s[2])) > 1.0e-8:
                return t, q, s
        except Exception:
            pass
    return _decompose_local_trs(_fbxsdk_matrix4_tuple(mat_obj))


def _fbxsdk_time_bucket(t_seconds: float) -> int:
    return int(round(float(t_seconds) / float(_TIME_EPSILON)))


def _fbxsdk_eval_local_trs_samples(
    node_obj: Any,
    sample_times: Sequence[float],
    fbx_mod: Any,
) -> Tuple[
    Dict[float, Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]],
    Dict[int, Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]],
]:
    exact: Dict[
        float,
        Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]],
    ] = {}
    bucketed: Dict[
        int,
        Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]],
    ] = {}
    if not sample_times:
        return exact, bucketed

    time_obj = None
    for t in sample_times:
        t_value = float(t)
        try:
            if time_obj is None:
                time_obj = fbx_mod.FbxTime()
            time_obj.SetSecondDouble(t_value)
            mat = node_obj.EvaluateLocalTransform(time_obj)
        except Exception:
            mat = None
        sample = _fbxsdk_local_trs_from_matrix(mat)
        exact[t_value] = sample
        bucketed.setdefault(_fbxsdk_time_bucket(t_value), sample)
    return exact, bucketed


def _fbxsdk_local_trs_from_sample_cache(
    node_obj: Any,
    t_seconds: float,
    fbx_mod: Any,
    exact_samples: Dict[
        float,
        Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]],
    ],
    bucketed_samples: Dict[
        int,
        Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]],
    ],
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float, float], Tuple[float, float, float]]:
    t_value = float(t_seconds)
    sample = exact_samples.get(t_value)
    if sample is not None:
        return sample
    sample = bucketed_samples.get(_fbxsdk_time_bucket(t_value))
    if sample is not None:
        return sample
    sample = _fbxsdk_eval_local_trs(node_obj, t_value, fbx_mod)
    exact_samples[t_value] = sample
    bucketed_samples.setdefault(_fbxsdk_time_bucket(t_value), sample)
    return sample


def _build_clip_from_fbxsdk_stack(
    *,
    fbx_mod: Any,
    scene_obj: Any,
    stack_obj: Any,
    stack_index: int,
    root_node: Any,
    skeleton: SkeletonAsset | None,
    warnings: List[str],
) -> AnimationClip | None:
    try:
        scene_obj.SetCurrentAnimationStack(stack_obj)
    except Exception:
        pass

    layers = _fbxsdk_list_anim_layers(fbx_mod, stack_obj)
    if not layers:
        warnings.append(
            f"clip_{stack_index}: animation stack has no layers and was skipped."
        )
        return None

    components = _fbxsdk_curve_components(fbx_mod)
    nodes = _fbxsdk_collect_nodes(root_node)
    skeleton_names = set(skeleton.joint_names) if skeleton is not None else None
    filtered_count = 0

    tracks: List[JointAnimationTrack] = []
    all_times: List[float] = []
    for node in nodes:
        joint_name = _safe_text(getattr(node, "GetName", lambda: "")(), "joint")
        t_times = _fbxsdk_property_curve_times(getattr(node, "LclTranslation", None), layers, components)
        r_times = _fbxsdk_property_curve_times(getattr(node, "LclRotation", None), layers, components)
        s_times = _fbxsdk_property_curve_times(getattr(node, "LclScaling", None), layers, components)
        if not (t_times or r_times or s_times):
            continue

        if skeleton_names is not None and joint_name not in skeleton_names:
            filtered_count += 1
            continue

        sample_times = _dedupe_times([*t_times, *r_times, *s_times])
        exact_samples, bucketed_samples = _fbxsdk_eval_local_trs_samples(
            node,
            sample_times,
            fbx_mod,
        )

        translation_keys = [
            Vec3Keyframe(
                time=float(t),
                value=_fbxsdk_local_trs_from_sample_cache(
                    node,
                    float(t),
                    fbx_mod,
                    exact_samples,
                    bucketed_samples,
                )[0],
                interpolation="linear",
            )
            for t in t_times
        ]
        rotation_keys = [
            QuatKeyframe(
                time=float(t),
                value=_fbxsdk_local_trs_from_sample_cache(
                    node,
                    float(t),
                    fbx_mod,
                    exact_samples,
                    bucketed_samples,
                )[1],
                interpolation="linear",
            )
            for t in r_times
        ]
        scale_keys = [
            Vec3Keyframe(
                time=float(t),
                value=_fbxsdk_local_trs_from_sample_cache(
                    node,
                    float(t),
                    fbx_mod,
                    exact_samples,
                    bucketed_samples,
                )[2],
                interpolation="linear",
            )
            for t in s_times
        ]
        tracks.append(
            JointAnimationTrack(
                joint_name=joint_name,
                translation_keys=translation_keys,
                rotation_keys=rotation_keys,
                scale_keys=scale_keys,
            )
        )
        all_times.extend(t_times)
        all_times.extend(r_times)
        all_times.extend(s_times)

    if filtered_count > 0:
        warnings.append(
            f"clip_{stack_index}: {filtered_count} track(s) were ignored because they are not part of the active skeleton."
        )
    if not tracks:
        return None

    tracks.sort(key=lambda track: track.joint_name.lower())
    clip_name = _safe_text(getattr(stack_obj, "GetName", lambda: "")(), f"clip_{stack_index}")

    span = _fbxsdk_stack_time_span_seconds(fbx_mod, stack_obj)
    if all_times:
        min_time = min(all_times)
        max_time = max(all_times)
    else:
        min_time = 0.0
        max_time = 0.0
    if span is not None:
        start_time, end_time = span
        if all_times:
            start_time = min(start_time, min_time)
            end_time = max(end_time, max_time)
    else:
        start_time = min_time
        end_time = max_time
    if end_time < start_time:
        end_time = start_time

    sample_rate = _fbxsdk_scene_sample_rate(
        scene_obj,
        fbx_mod,
        fallback=_derive_sample_rate_hz(all_times, 30.0),
    )
    clip = AnimationClip(
        name=clip_name,
        start_time=float(start_time),
        end_time=float(end_time),
        sample_rate_hz=float(sample_rate),
        tracks=tracks,
        metadata={
            "source_backend": "fbx_sdk",
            "source_stack_index": int(stack_index),
        },
    )
    clip.validate(skeleton=skeleton)
    return clip


def _ingest_fbxsdk_scene(
    *,
    path_obj: Path,
    fbx_mod: Any,
    scene_obj: Any,
    skeleton: SkeletonAsset | None,
) -> FBXAnimationIngestResult:
    warnings: List[str] = []
    try:
        root_node = scene_obj.GetRootNode()
    except Exception as exc:
        raise FBXAnimationIngestError(f"FBX SDK scene has no root node: {exc}") from exc
    if root_node is None:
        raise FBXAnimationIngestError("FBX SDK scene has no root node.")

    stacks = _fbxsdk_list_animation_stacks(fbx_mod, scene_obj)
    clips: List[AnimationClip] = []
    for stack_index, stack in enumerate(stacks):
        clip = _build_clip_from_fbxsdk_stack(
            fbx_mod=fbx_mod,
            scene_obj=scene_obj,
            stack_obj=stack,
            stack_index=stack_index,
            root_node=root_node,
            skeleton=skeleton,
            warnings=warnings,
        )
        if clip is not None:
            clips.append(clip)

    if not clips:
        warnings.append("No animation clips were found in source.")
    return FBXAnimationIngestResult(
        source_path=str(path_obj),
        clips=clips,
        warnings=warnings,
    )


def _animation_path_cache_token(path_obj: Path) -> Tuple[str, int, int]:
    try:
        resolved = str(path_obj.resolve())
    except Exception:
        resolved = str(path_obj)
    if os.name == "nt":
        resolved = resolved.lower()
    stat = path_obj.stat()
    return (resolved, int(stat.st_mtime_ns), int(stat.st_size))


def _skeleton_cache_signature(skeleton: SkeletonAsset | None) -> Tuple[str, ...]:
    if skeleton is None:
        return ("<none>",)
    try:
        names = tuple(str(name) for name in list(getattr(skeleton, "joint_names", []) or []))
    except Exception:
        names = ()
    if names:
        return names
    try:
        return tuple(
            str(getattr(joint, "name", "") or "")
            for joint in list(getattr(skeleton, "joints", []) or [])
        )
    except Exception:
        return ("<unknown>",)


def _animation_cache_key(path_obj: Path, skeleton: SkeletonAsset | None) -> Tuple[str, int, int, Tuple[str, ...]]:
    path_token = _animation_path_cache_token(path_obj)
    return (
        path_token[0],
        path_token[1],
        path_token[2],
        _skeleton_cache_signature(skeleton),
    )


def _animation_cache_get(key: Tuple[str, int, int, Tuple[str, ...]]) -> FBXAnimationIngestResult | None:
    cached = _ANIMATION_INGEST_CACHE.get(key)
    if cached is None:
        return None
    _ANIMATION_INGEST_CACHE.move_to_end(key)
    return cached


def _animation_cache_put(
    key: Tuple[str, int, int, Tuple[str, ...]],
    value: FBXAnimationIngestResult,
) -> None:
    _ANIMATION_INGEST_CACHE[key] = value
    _ANIMATION_INGEST_CACHE.move_to_end(key)
    while len(_ANIMATION_INGEST_CACHE) > int(_ANIMATION_INGEST_CACHE_MAX):
        _ANIMATION_INGEST_CACHE.popitem(last=False)


def ingest_fbx_animation_data(
    path: str | Path,
    *,
    skeleton: SkeletonAsset | None = None,
    scene: Any | None = None,
) -> FBXAnimationIngestResult:
    path_obj = Path(path)
    if skeleton is not None:
        skeleton.validate()

    if scene is not None:
        return _ingest_pyassimp_scene(
            path_obj=path_obj,
            scene_obj=scene,
            skeleton=skeleton,
        )

    if not path_obj.exists():
        raise FBXAnimationIngestError(f"FBX path does not exist: {path_obj}")
    cache_key = _animation_cache_key(path_obj, skeleton)
    cached = _animation_cache_get(cache_key)
    if cached is not None:
        return cached

    failures: List[str] = []
    try:
        with _load_fbxsdk_scene(path_obj) as (fbx_mod, scene_obj):
            result = _ingest_fbxsdk_scene(
                path_obj=path_obj,
                fbx_mod=fbx_mod,
                scene_obj=scene_obj,
                skeleton=skeleton,
            )
            _animation_cache_put(cache_key, result)
            return result
    except FBXAnimationIngestError as exc:
        failures.append(f"fbx sdk: {exc}")

    try:
        with _load_pyassimp_scene(path_obj) as scene_obj:
            result = _ingest_pyassimp_scene(
                path_obj=path_obj,
                scene_obj=scene_obj,
                skeleton=skeleton,
            )
            _animation_cache_put(cache_key, result)
            return result
    except FBXAnimationIngestError as exc:
        failures.append(f"pyassimp: {exc}")

    raise FBXAnimationIngestError(
        "Failed to load FBX animation with available backends: " + " | ".join(failures)
    )


__all__ = [
    "FBXAnimationIngestError",
    "FBXAnimationIngestResult",
    "ingest_fbx_animation_data",
]
