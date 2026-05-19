from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import re
from typing import Dict, List, Sequence, Tuple

from .fbx_canonical import (
    AnimationClip,
    Joint,
    JointAnimationTrack,
    JointTransform,
    QuatKeyframe,
    SkeletonAsset,
    Vec3Keyframe,
)
from echograph.services.profiler import profiled

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
_BVH_INGEST_CACHE: Dict[Tuple[str, int, int, Tuple[Tuple[str, int], ...]], "BVHAnimationIngestResult"] = {}
_BVH_INGEST_CACHE_LIMIT = 32


class BVHIngestError(RuntimeError):
    """Raised when deterministic BVH ingest fails."""


@dataclass(eq=True)
class BVHAnimationIngestResult:
    source_path: str
    skeleton: SkeletonAsset
    clips: List[AnimationClip] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass(eq=True)
class _ParsedBVHJoint:
    name: str
    parent_index: int
    offset: Tuple[float, float, float]
    channels: List[str] = field(default_factory=list)
    end_sites: List[Tuple[float, float, float]] = field(default_factory=list)


@dataclass(eq=True)
class _ParsedBVH:
    joints: List[_ParsedBVHJoint]
    frames: List[List[float]]
    frame_time: float

    @property
    def channel_count(self) -> int:
        return sum(len(joint.channels) for joint in self.joints)


def _path_cache_token(path_obj: Path) -> Tuple[str, int, int]:
    try:
        resolved = str(path_obj.resolve())
    except Exception:
        resolved = str(path_obj)
    try:
        stat = path_obj.stat()
        return (resolved, int(getattr(stat, "st_mtime_ns", 0) or 0), int(stat.st_size))
    except Exception:
        return (resolved, 0, 0)


def _skeleton_cache_signature(skeleton: SkeletonAsset | None) -> Tuple[Tuple[str, int], ...]:
    if skeleton is None:
        return ()
    out = []
    for joint in list(getattr(skeleton, "joints", []) or []):
        try:
            parent_index = int(getattr(joint, "parent_index", -1))
        except Exception:
            parent_index = -1
        out.append(
            (
                str(getattr(joint, "name", "") or ""),
                parent_index,
            )
        )
    return tuple(out)


def _ingest_cache_key(
    path_obj: Path,
    skeleton: SkeletonAsset | None,
) -> Tuple[str, int, int, Tuple[Tuple[str, int], ...]]:
    path_text, mtime_ns, size = _path_cache_token(path_obj)
    return (path_text, mtime_ns, size, _skeleton_cache_signature(skeleton))


def _ingest_cache_get(
    key: Tuple[str, int, int, Tuple[Tuple[str, int], ...]],
) -> "BVHAnimationIngestResult | None":
    return _BVH_INGEST_CACHE.get(key)


def _ingest_cache_put(
    key: Tuple[str, int, int, Tuple[Tuple[str, int], ...]],
    result: "BVHAnimationIngestResult",
) -> None:
    _BVH_INGEST_CACHE[key] = result
    if len(_BVH_INGEST_CACHE) <= _BVH_INGEST_CACHE_LIMIT:
        return
    try:
        oldest = next(iter(_BVH_INGEST_CACHE.keys()))
        _BVH_INGEST_CACHE.pop(oldest, None)
    except Exception:
        pass


def _safe_name(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _to_float(value: str, context: str) -> float:
    try:
        return float(value)
    except Exception as exc:
        raise BVHIngestError(f"{context} must be a number, got {value!r}.") from exc


def _to_int(value: str, context: str) -> int:
    try:
        return int(value)
    except Exception as exc:
        raise BVHIngestError(f"{context} must be an integer, got {value!r}.") from exc


def _quat_normalize(q: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x, y, z, w = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
    n2 = (x * x) + (y * y) + (z * z) + (w * w)
    if n2 <= 1e-16:
        return (0.0, 0.0, 0.0, 1.0)
    inv = 1.0 / math.sqrt(n2)
    return (x * inv, y * inv, z * inv, w * inv)


def _quat_mul(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return _quat_normalize(
        (
            (aw * bx) + (ax * bw) + (ay * bz) - (az * by),
            (aw * by) - (ax * bz) + (ay * bw) + (az * bx),
            (aw * bz) + (ax * by) - (ay * bx) + (az * bw),
            (aw * bw) - (ax * bx) - (ay * by) - (az * bz),
        )
    )


def _axis_quat(axis: str, degrees: float) -> Tuple[float, float, float, float]:
    half = math.radians(float(degrees)) * 0.5
    s = math.sin(half)
    c = math.cos(half)
    key = str(axis or "").strip().lower()
    if key == "x":
        return (s, 0.0, 0.0, c)
    if key == "y":
        return (0.0, s, 0.0, c)
    if key == "z":
        return (0.0, 0.0, s, c)
    raise BVHIngestError(f"Unsupported BVH rotation axis: {axis!r}.")


def _quat_from_bvh_rotation_channels(
    rotation_values: Sequence[Tuple[str, float]],
) -> Tuple[float, float, float, float]:
    q = (0.0, 0.0, 0.0, 1.0)
    for axis, degrees in rotation_values:
        q = _quat_mul(q, _axis_quat(axis, float(degrees)))
    return _quat_normalize(q)


class _HierarchyTokenParser:
    def __init__(self, tokens: Sequence[str]) -> None:
        self._tokens = list(tokens)
        self._pos = 0
        self.joints: List[_ParsedBVHJoint] = []

    def _peek(self) -> str:
        if self._pos >= len(self._tokens):
            return ""
        return self._tokens[self._pos]

    def _pop(self, context: str) -> str:
        if self._pos >= len(self._tokens):
            raise BVHIngestError(f"Unexpected end of BVH hierarchy while reading {context}.")
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _expect(self, expected: str, context: str) -> None:
        token = self._pop(context)
        if token.lower() != expected.lower():
            raise BVHIngestError(f"Expected {expected!r} while reading {context}, got {token!r}.")

    def parse(self) -> List[_ParsedBVHJoint]:
        self._expect("HIERARCHY", "file header")
        self._parse_joint(parent_index=-1, first_token=self._pop("root joint"))
        if self._pos < len(self._tokens):
            extra = self._tokens[self._pos]
            raise BVHIngestError(f"Unexpected token after hierarchy: {extra!r}.")
        if not self.joints:
            raise BVHIngestError("BVH hierarchy contains no joints.")
        return self.joints

    def _parse_joint(self, parent_index: int, first_token: str) -> int:
        kind = str(first_token or "").strip()
        if kind not in {"ROOT", "JOINT"}:
            raise BVHIngestError(f"Expected ROOT or JOINT, got {kind!r}.")
        name = _safe_name(self._pop(f"{kind} name"), f"joint_{len(self.joints)}")
        self._expect("{", f"{kind} {name}")

        joint_index = len(self.joints)
        self.joints.append(
            _ParsedBVHJoint(
                name=name,
                parent_index=int(parent_index),
                offset=(0.0, 0.0, 0.0),
                channels=[],
            )
        )

        while True:
            token = self._pop(f"{kind} {name} block")
            upper = token.upper()
            if token == "}":
                break
            if upper == "OFFSET":
                self.joints[joint_index].offset = (
                    _to_float(self._pop("OFFSET x"), "OFFSET x"),
                    _to_float(self._pop("OFFSET y"), "OFFSET y"),
                    _to_float(self._pop("OFFSET z"), "OFFSET z"),
                )
                continue
            if upper == "CHANNELS":
                count = _to_int(self._pop("CHANNELS count"), "CHANNELS count")
                if count < 0:
                    raise BVHIngestError("CHANNELS count cannot be negative.")
                channels = [self._pop(f"CHANNELS item {i}") for i in range(count)]
                self.joints[joint_index].channels = channels
                continue
            if upper == "JOINT":
                self._parse_joint(parent_index=joint_index, first_token="JOINT")
                continue
            if upper == "END":
                self._expect("Site", "End Site")
                self._parse_end_site(joint_index)
                continue
            raise BVHIngestError(f"Unexpected token in joint {name!r}: {token!r}.")

        return joint_index

    def _parse_end_site(self, joint_index: int) -> None:
        self._expect("{", "End Site")
        offset = (0.0, 0.0, 0.0)
        while True:
            token = self._pop("End Site block")
            if token == "}":
                break
            if token.upper() == "OFFSET":
                offset = (
                    _to_float(self._pop("End Site OFFSET x"), "End Site OFFSET x"),
                    _to_float(self._pop("End Site OFFSET y"), "End Site OFFSET y"),
                    _to_float(self._pop("End Site OFFSET z"), "End Site OFFSET z"),
                )
                continue
            raise BVHIngestError(f"Unexpected token in End Site: {token!r}.")
        self.joints[joint_index].end_sites.append(offset)


def _split_bvh_sections(text: str) -> Tuple[str, str]:
    match = re.search(r"(?im)^\s*MOTION\s*$", text)
    if match is None:
        raise BVHIngestError("BVH file is missing MOTION section.")
    hierarchy_text = text[: match.start()]
    motion_text = text[match.end() :]
    return hierarchy_text, motion_text


def _parse_hierarchy(text: str) -> List[_ParsedBVHJoint]:
    tokens = re.findall(r"[{}]|[^\s{}]+", text)
    if not tokens:
        raise BVHIngestError("BVH file is empty.")
    parser = _HierarchyTokenParser(tokens)
    return parser.parse()


def _parse_motion(text: str, expected_channels: int) -> Tuple[List[List[float]], float]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise BVHIngestError("BVH MOTION section is incomplete.")

    frames_match = re.match(r"(?i)^Frames\s*:\s*(\d+)\s*$", lines[0])
    if frames_match is None:
        raise BVHIngestError("BVH MOTION section is missing Frames line.")
    frame_count = _to_int(frames_match.group(1), "Frames")

    frame_time_match = re.match(r"(?i)^Frame\s+Time\s*:\s*([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)\s*$", lines[1])
    if frame_time_match is None:
        raise BVHIngestError("BVH MOTION section is missing Frame Time line.")
    frame_time = _to_float(frame_time_match.group(1), "Frame Time")
    if frame_count < 0:
        raise BVHIngestError("Frames cannot be negative.")
    if frame_time <= 0.0:
        raise BVHIngestError("Frame Time must be > 0.")

    frame_lines = lines[2:]
    if len(frame_lines) != frame_count:
        raise BVHIngestError(
            f"BVH frame count mismatch: header says {frame_count}, found {len(frame_lines)} rows."
        )

    frames: List[List[float]] = []
    for frame_index, line in enumerate(frame_lines):
        values = [_to_float(value, f"frame {frame_index} value") for value in line.split()]
        if len(values) != expected_channels:
            raise BVHIngestError(
                f"BVH frame {frame_index} has {len(values)} values; expected {expected_channels}."
            )
        frames.append(values)
    return frames, frame_time


def parse_bvh_text(text: str) -> _ParsedBVH:
    hierarchy_text, motion_text = _split_bvh_sections(str(text or ""))
    joints = _parse_hierarchy(hierarchy_text)
    expected_channels = sum(len(joint.channels) for joint in joints)
    frames, frame_time = _parse_motion(motion_text, expected_channels)
    return _ParsedBVH(joints=joints, frames=frames, frame_time=frame_time)


def _skeleton_from_parsed(parsed: _ParsedBVH, skeleton_name: str) -> SkeletonAsset:
    joints: List[Joint] = []
    for joint in parsed.joints:
        joints.append(
            Joint(
                name=joint.name,
                parent_index=int(joint.parent_index),
                local_bind=JointTransform(
                    translation=tuple(joint.offset),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    scale=(1.0, 1.0, 1.0),
                ),
                inverse_bind_matrix=_IDENTITY_MATRIX_4X4,
            )
        )
    skeleton = SkeletonAsset(
        name=skeleton_name,
        joints=joints,
        metadata={
            "source_format": "bvh",
            "frame_count": int(len(parsed.frames)),
            "frame_time": float(parsed.frame_time),
            "channels": {joint.name: list(joint.channels) for joint in parsed.joints},
            "end_sites": {
                joint.name: [list(offset) for offset in joint.end_sites]
                for joint in parsed.joints
                if joint.end_sites
            },
        },
    )
    skeleton.validate()
    return skeleton


def _build_clip_from_parsed(
    parsed: _ParsedBVH,
    *,
    clip_name: str,
    skeleton: SkeletonAsset,
    target_skeleton: SkeletonAsset | None,
    warnings: List[str],
) -> AnimationClip | None:
    frame_count = len(parsed.frames)
    if frame_count <= 0:
        warnings.append("No BVH motion frames were found.")
        return None

    target_names = set(target_skeleton.joint_names) if target_skeleton is not None else None
    filtered_count = 0
    tracks: List[JointAnimationTrack] = []

    channel_offsets: List[int] = []
    cursor = 0
    for joint in parsed.joints:
        channel_offsets.append(cursor)
        cursor += len(joint.channels)

    for joint_index, parsed_joint in enumerate(parsed.joints):
        joint_name = parsed_joint.name
        if target_names is not None and joint_name not in target_names:
            filtered_count += 1
            continue

        channels = list(parsed_joint.channels)
        if not channels:
            continue

        start = channel_offsets[joint_index]
        bind_translation = tuple(skeleton.joints[joint_index].local_bind.translation)
        has_position = any(str(ch).strip().lower().endswith("position") for ch in channels)
        has_rotation = any(str(ch).strip().lower().endswith("rotation") for ch in channels)

        translation_keys: List[Vec3Keyframe] = []
        rotation_keys: List[QuatKeyframe] = []
        for frame_index, frame_values in enumerate(parsed.frames):
            t = float(frame_index) * float(parsed.frame_time)
            if has_position:
                tx, ty, tz = bind_translation
                for channel_index, channel in enumerate(channels):
                    key = str(channel or "").strip().lower()
                    value = float(frame_values[start + channel_index])
                    if key == "xposition":
                        tx = value
                    elif key == "yposition":
                        ty = value
                    elif key == "zposition":
                        tz = value
                translation_keys.append(
                    Vec3Keyframe(
                        time=t,
                        value=(float(tx), float(ty), float(tz)),
                        interpolation="linear",
                    )
                )

            if has_rotation:
                rotation_values: List[Tuple[str, float]] = []
                for channel_index, channel in enumerate(channels):
                    key = str(channel or "").strip().lower()
                    if key in {"xrotation", "yrotation", "zrotation"}:
                        rotation_values.append((key[0], float(frame_values[start + channel_index])))
                rotation_keys.append(
                    QuatKeyframe(
                        time=t,
                        value=_quat_from_bvh_rotation_channels(rotation_values),
                        interpolation="linear",
                    )
                )

        if translation_keys or rotation_keys:
            tracks.append(
                JointAnimationTrack(
                    joint_name=joint_name,
                    translation_keys=translation_keys,
                    rotation_keys=rotation_keys,
                    scale_keys=[],
                )
            )

    if filtered_count > 0:
        warnings.append(
            f"{filtered_count} BVH track(s) were ignored because they are not part of the active skeleton."
        )

    if not tracks:
        warnings.append("No BVH animation tracks matched the active skeleton.")
        return None

    end_time = max(0.0, float(frame_count - 1) * float(parsed.frame_time))
    sample_rate = 1.0 / float(parsed.frame_time) if parsed.frame_time > _TIME_EPSILON else 30.0
    clip = AnimationClip(
        name=clip_name,
        start_time=0.0,
        end_time=end_time,
        sample_rate_hz=max(1.0, float(sample_rate)),
        tracks=tracks,
        metadata={
            "source_backend": "bvh_parser",
            "source_format": "bvh",
            "frame_count": int(frame_count),
            "frame_time": float(parsed.frame_time),
        },
    )
    clip.validate(skeleton=target_skeleton or skeleton)
    return clip


@profiled("rigging.bvh_ingest")
def ingest_bvh_animation_data(
    path: str | Path,
    *,
    skeleton: SkeletonAsset | None = None,
) -> BVHAnimationIngestResult:
    path_obj = Path(path)
    if not path_obj.exists():
        raise BVHIngestError(f"BVH path does not exist: {path_obj}")
    if path_obj.suffix.lower() != ".bvh":
        raise BVHIngestError(f"Path is not a .bvh file: {path_obj}")
    if skeleton is not None:
        skeleton.validate()
    cache_key = _ingest_cache_key(path_obj, skeleton)
    cached = _ingest_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        text = path_obj.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        raise BVHIngestError(f"Failed to read BVH file: {path_obj}") from exc

    parsed = parse_bvh_text(text)
    skeleton_name = _safe_name(path_obj.stem, "BVHSkeleton")
    parsed_skeleton = _skeleton_from_parsed(parsed, skeleton_name=skeleton_name)
    warnings: List[str] = []
    clip = _build_clip_from_parsed(
        parsed,
        clip_name=_safe_name(path_obj.stem, "bvh_clip"),
        skeleton=parsed_skeleton,
        target_skeleton=skeleton,
        warnings=warnings,
    )
    clips = [clip] if clip is not None else []
    result = BVHAnimationIngestResult(
        source_path=str(path_obj),
        skeleton=parsed_skeleton,
        clips=clips,
        warnings=warnings,
    )
    _ingest_cache_put(cache_key, result)
    return result


__all__ = [
    "BVHIngestError",
    "BVHAnimationIngestResult",
    "parse_bvh_text",
    "ingest_bvh_animation_data",
]
