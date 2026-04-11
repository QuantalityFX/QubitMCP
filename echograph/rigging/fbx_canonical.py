from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

_WEIGHT_EPSILON = 1e-4
_TIME_EPSILON = 1e-8
_VALID_INTERPOLATION = {"step", "linear", "cubic"}
_IDENTITY_MATRIX_4X4 = (
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


class SchemaValidationError(ValueError):
    """Raised when canonical rig schema validation fails."""


def _ensure_non_empty_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{field_name} must be a non-empty string.")


def _ensure_float_tuple(
    values: Sequence[Any], expected_len: int, field_name: str
) -> Tuple[float, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise SchemaValidationError(f"{field_name} must be a sequence of floats.")
    if len(values) != expected_len:
        raise SchemaValidationError(
            f"{field_name} must have {expected_len} items, got {len(values)}."
        )
    try:
        return tuple(float(v) for v in values)
    except Exception as exc:  # pragma: no cover - defensive
        raise SchemaValidationError(f"{field_name} must contain only numbers.") from exc


def _ensure_increasing_key_times(keys: Sequence[Any], field_name: str) -> None:
    last_t: float | None = None
    for i, key in enumerate(keys):
        if hasattr(key, "time"):
            t = float(getattr(key, "time"))
        elif isinstance(key, dict) and "time" in key:
            t = float(key["time"])
        else:
            raise SchemaValidationError(f"{field_name} key at index {i} is missing time.")
        if last_t is not None and t <= last_t + _TIME_EPSILON:
            raise SchemaValidationError(
                f"{field_name} key times must be strictly increasing (index {i})."
            )
        last_t = t


@dataclass(eq=True)
class JointTransform:
    translation: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)

    def validate(self, context: str = "JointTransform") -> None:
        self.translation = _ensure_float_tuple(self.translation, 3, f"{context}.translation")  # type: ignore[assignment]
        self.rotation = _ensure_float_tuple(self.rotation, 4, f"{context}.rotation")  # type: ignore[assignment]
        self.scale = _ensure_float_tuple(self.scale, 3, f"{context}.scale")  # type: ignore[assignment]

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "translation": list(self.translation),
            "rotation": list(self.rotation),
            "scale": list(self.scale),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "JointTransform":
        xf = cls(
            translation=tuple(payload.get("translation", (0.0, 0.0, 0.0))),  # type: ignore[arg-type]
            rotation=tuple(payload.get("rotation", (0.0, 0.0, 0.0, 1.0))),  # type: ignore[arg-type]
            scale=tuple(payload.get("scale", (1.0, 1.0, 1.0))),  # type: ignore[arg-type]
        )
        xf.validate()
        return xf


@dataclass(eq=True)
class Joint:
    name: str
    parent_index: int
    local_bind: JointTransform = field(default_factory=JointTransform)
    inverse_bind_matrix: Tuple[float, ...] = _IDENTITY_MATRIX_4X4

    def validate(self, joint_index: int) -> None:
        _ensure_non_empty_text(self.name, f"Joint[{joint_index}].name")
        if not isinstance(self.parent_index, int):
            raise SchemaValidationError(f"Joint[{joint_index}].parent_index must be int.")
        self.local_bind.validate(context=f"Joint[{joint_index}].local_bind")
        self.inverse_bind_matrix = _ensure_float_tuple(
            self.inverse_bind_matrix,
            16,
            f"Joint[{joint_index}].inverse_bind_matrix",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parent_index": int(self.parent_index),
            "local_bind": self.local_bind.to_dict(),
            "inverse_bind_matrix": list(self.inverse_bind_matrix),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Joint":
        joint = cls(
            name=str(payload.get("name", "")),
            parent_index=int(payload.get("parent_index", -1)),
            local_bind=JointTransform.from_dict(
                dict(payload.get("local_bind") or {})
            ),
            inverse_bind_matrix=tuple(payload.get("inverse_bind_matrix", _IDENTITY_MATRIX_4X4)),  # type: ignore[arg-type]
        )
        return joint


@dataclass(eq=True)
class SkeletonAsset:
    name: str
    joints: List[Joint] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        _ensure_non_empty_text(self.name, "SkeletonAsset.name")
        if not self.joints:
            raise SchemaValidationError("SkeletonAsset.joints cannot be empty.")

        names_seen: set[str] = set()
        root_count = 0
        for i, joint in enumerate(self.joints):
            joint.validate(i)

            if joint.name in names_seen:
                raise SchemaValidationError(
                    f"SkeletonAsset has duplicate joint name: {joint.name!r}."
                )
            names_seen.add(joint.name)

            p = joint.parent_index
            if p == -1:
                root_count += 1
            elif p < 0 or p >= len(self.joints):
                raise SchemaValidationError(
                    f"Joint[{i}].parent_index out of range: {p}."
                )
            elif p >= i:
                raise SchemaValidationError(
                    f"Joint[{i}] parent must appear earlier in the list for stable hierarchy order."
                )

        if root_count == 0:
            raise SchemaValidationError("SkeletonAsset must contain at least one root joint.")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "name": self.name,
            "joints": [j.to_dict() for j in self.joints],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SkeletonAsset":
        asset = cls(
            name=str(payload.get("name", "")),
            joints=[
                Joint.from_dict(dict(j))
                for j in list(payload.get("joints") or [])
            ],
            metadata=dict(payload.get("metadata") or {}),
        )
        asset.validate()
        return asset

    @property
    def joint_names(self) -> List[str]:
        return [j.name for j in self.joints]


@dataclass(eq=True)
class VertexInfluence:
    joint_index: int
    weight: float

    def validate(self, context: str, skeleton_joint_count: int) -> None:
        if not isinstance(self.joint_index, int):
            raise SchemaValidationError(f"{context}.joint_index must be int.")
        if self.joint_index < 0 or self.joint_index >= skeleton_joint_count:
            raise SchemaValidationError(
                f"{context}.joint_index out of range: {self.joint_index}."
            )
        self.weight = float(self.weight)
        if self.weight <= 0.0 or self.weight > 1.0 + _WEIGHT_EPSILON:
            raise SchemaValidationError(f"{context}.weight must be in (0, 1].")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "joint_index": int(self.joint_index),
            "weight": float(self.weight),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "VertexInfluence":
        return cls(
            joint_index=int(payload.get("joint_index", -1)),
            weight=float(payload.get("weight", 0.0)),
        )


@dataclass(eq=True)
class VertexSkin:
    vertex_index: int
    influences: List[VertexInfluence] = field(default_factory=list)

    def validate(self, vertex_count: int, skeleton_joint_count: int, context: str) -> None:
        if not isinstance(self.vertex_index, int):
            raise SchemaValidationError(f"{context}.vertex_index must be int.")
        if self.vertex_index < 0 or self.vertex_index >= vertex_count:
            raise SchemaValidationError(
                f"{context}.vertex_index out of range: {self.vertex_index}."
            )
        if not self.influences:
            raise SchemaValidationError(f"{context}.influences cannot be empty.")

        joint_seen: set[int] = set()
        total_weight = 0.0
        for i, inf in enumerate(self.influences):
            inf.validate(
                context=f"{context}.influences[{i}]",
                skeleton_joint_count=skeleton_joint_count,
            )
            if inf.joint_index in joint_seen:
                raise SchemaValidationError(
                    f"{context} has duplicate influence for joint {inf.joint_index}."
                )
            joint_seen.add(inf.joint_index)
            total_weight += inf.weight

        if abs(total_weight - 1.0) > _WEIGHT_EPSILON:
            raise SchemaValidationError(
                f"{context} influence weights must sum to 1.0, got {total_weight:.6f}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vertex_index": int(self.vertex_index),
            "influences": [i.to_dict() for i in self.influences],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "VertexSkin":
        return cls(
            vertex_index=int(payload.get("vertex_index", -1)),
            influences=[
                VertexInfluence.from_dict(dict(i))
                for i in list(payload.get("influences") or [])
            ],
        )


@dataclass(eq=True)
class SkeletalMeshAsset:
    name: str
    skeleton_name: str
    vertex_count: int
    triangle_indices: List[int] = field(default_factory=list)
    vertex_skins: List[VertexSkin] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self, skeleton: SkeletonAsset | None = None) -> None:
        _ensure_non_empty_text(self.name, "SkeletalMeshAsset.name")
        _ensure_non_empty_text(self.skeleton_name, "SkeletalMeshAsset.skeleton_name")
        if not isinstance(self.vertex_count, int) or self.vertex_count <= 0:
            raise SchemaValidationError("SkeletalMeshAsset.vertex_count must be > 0.")

        if len(self.triangle_indices) % 3 != 0:
            raise SchemaValidationError(
                "SkeletalMeshAsset.triangle_indices length must be divisible by 3."
            )
        for i, vid in enumerate(self.triangle_indices):
            if not isinstance(vid, int):
                raise SchemaValidationError(
                    f"SkeletalMeshAsset.triangle_indices[{i}] must be int."
                )
            if vid < 0 or vid >= self.vertex_count:
                raise SchemaValidationError(
                    f"SkeletalMeshAsset.triangle_indices[{i}] out of range: {vid}."
                )

        skeleton_joint_count = 0
        if skeleton is not None:
            skeleton.validate()
            skeleton_joint_count = len(skeleton.joints)
            if self.skeleton_name != skeleton.name:
                raise SchemaValidationError(
                    "SkeletalMeshAsset.skeleton_name does not match provided SkeletonAsset.name."
                )
        else:
            max_joint = -1
            for skin in self.vertex_skins:
                for inf in skin.influences:
                    max_joint = max(max_joint, int(inf.joint_index))
            skeleton_joint_count = max_joint + 1

        if skeleton_joint_count <= 0:
            raise SchemaValidationError(
                "SkeletalMeshAsset needs at least one valid joint reference in vertex_skins."
            )

        seen_vertices: set[int] = set()
        for i, skin in enumerate(self.vertex_skins):
            skin.validate(
                vertex_count=self.vertex_count,
                skeleton_joint_count=skeleton_joint_count,
                context=f"SkeletalMeshAsset.vertex_skins[{i}]",
            )
            if skin.vertex_index in seen_vertices:
                raise SchemaValidationError(
                    f"SkeletalMeshAsset has duplicate skin entry for vertex {skin.vertex_index}."
                )
            seen_vertices.add(skin.vertex_index)

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "name": self.name,
            "skeleton_name": self.skeleton_name,
            "vertex_count": int(self.vertex_count),
            "triangle_indices": [int(i) for i in self.triangle_indices],
            "vertex_skins": [v.to_dict() for v in self.vertex_skins],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "SkeletalMeshAsset":
        asset = cls(
            name=str(payload.get("name", "")),
            skeleton_name=str(payload.get("skeleton_name", "")),
            vertex_count=int(payload.get("vertex_count", 0)),
            triangle_indices=[int(i) for i in list(payload.get("triangle_indices") or [])],
            vertex_skins=[
                VertexSkin.from_dict(dict(v))
                for v in list(payload.get("vertex_skins") or [])
            ],
            metadata=dict(payload.get("metadata") or {}),
        )
        asset.validate()
        return asset


@dataclass(eq=True)
class Vec3Keyframe:
    time: float
    value: Tuple[float, float, float]
    interpolation: str = "linear"

    def validate(self, context: str, start_time: float, end_time: float) -> None:
        self.time = float(self.time)
        if self.time < start_time - _TIME_EPSILON or self.time > end_time + _TIME_EPSILON:
            raise SchemaValidationError(f"{context}.time out of clip range.")
        self.value = _ensure_float_tuple(self.value, 3, f"{context}.value")  # type: ignore[assignment]
        self.interpolation = str(self.interpolation).strip().lower()
        if self.interpolation not in _VALID_INTERPOLATION:
            raise SchemaValidationError(
                f"{context}.interpolation must be one of {sorted(_VALID_INTERPOLATION)}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": float(self.time),
            "value": list(self.value),
            "interpolation": self.interpolation,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Vec3Keyframe":
        return cls(
            time=float(payload.get("time", 0.0)),
            value=tuple(payload.get("value", (0.0, 0.0, 0.0))),  # type: ignore[arg-type]
            interpolation=str(payload.get("interpolation", "linear")),
        )


@dataclass(eq=True)
class QuatKeyframe:
    time: float
    value: Tuple[float, float, float, float]
    interpolation: str = "linear"

    def validate(self, context: str, start_time: float, end_time: float) -> None:
        self.time = float(self.time)
        if self.time < start_time - _TIME_EPSILON or self.time > end_time + _TIME_EPSILON:
            raise SchemaValidationError(f"{context}.time out of clip range.")
        self.value = _ensure_float_tuple(self.value, 4, f"{context}.value")  # type: ignore[assignment]
        self.interpolation = str(self.interpolation).strip().lower()
        if self.interpolation not in _VALID_INTERPOLATION:
            raise SchemaValidationError(
                f"{context}.interpolation must be one of {sorted(_VALID_INTERPOLATION)}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": float(self.time),
            "value": list(self.value),
            "interpolation": self.interpolation,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "QuatKeyframe":
        return cls(
            time=float(payload.get("time", 0.0)),
            value=tuple(payload.get("value", (0.0, 0.0, 0.0, 1.0))),  # type: ignore[arg-type]
            interpolation=str(payload.get("interpolation", "linear")),
        )


@dataclass(eq=True)
class JointAnimationTrack:
    joint_name: str
    translation_keys: List[Vec3Keyframe] = field(default_factory=list)
    rotation_keys: List[QuatKeyframe] = field(default_factory=list)
    scale_keys: List[Vec3Keyframe] = field(default_factory=list)

    def validate(self, start_time: float, end_time: float, context: str) -> None:
        _ensure_non_empty_text(self.joint_name, f"{context}.joint_name")
        if not (self.translation_keys or self.rotation_keys or self.scale_keys):
            raise SchemaValidationError(f"{context} must contain at least one key channel.")

        for i, key in enumerate(self.translation_keys):
            key.validate(f"{context}.translation_keys[{i}]", start_time, end_time)
        for i, key in enumerate(self.rotation_keys):
            key.validate(f"{context}.rotation_keys[{i}]", start_time, end_time)
        for i, key in enumerate(self.scale_keys):
            key.validate(f"{context}.scale_keys[{i}]", start_time, end_time)

        _ensure_increasing_key_times(self.translation_keys, f"{context}.translation_keys")
        _ensure_increasing_key_times(self.rotation_keys, f"{context}.rotation_keys")
        _ensure_increasing_key_times(self.scale_keys, f"{context}.scale_keys")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "joint_name": self.joint_name,
            "translation_keys": [k.to_dict() for k in self.translation_keys],
            "rotation_keys": [k.to_dict() for k in self.rotation_keys],
            "scale_keys": [k.to_dict() for k in self.scale_keys],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "JointAnimationTrack":
        return cls(
            joint_name=str(payload.get("joint_name", "")),
            translation_keys=[
                Vec3Keyframe.from_dict(dict(k))
                for k in list(payload.get("translation_keys") or [])
            ],
            rotation_keys=[
                QuatKeyframe.from_dict(dict(k))
                for k in list(payload.get("rotation_keys") or [])
            ],
            scale_keys=[
                Vec3Keyframe.from_dict(dict(k))
                for k in list(payload.get("scale_keys") or [])
            ],
        )


@dataclass(eq=True)
class AnimationClip:
    name: str
    start_time: float
    end_time: float
    sample_rate_hz: float
    tracks: List[JointAnimationTrack] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self, skeleton: SkeletonAsset | None = None) -> None:
        _ensure_non_empty_text(self.name, "AnimationClip.name")
        self.start_time = float(self.start_time)
        self.end_time = float(self.end_time)
        self.sample_rate_hz = float(self.sample_rate_hz)

        if self.end_time < self.start_time - _TIME_EPSILON:
            raise SchemaValidationError("AnimationClip.end_time must be >= start_time.")
        if self.sample_rate_hz <= 0.0:
            raise SchemaValidationError("AnimationClip.sample_rate_hz must be > 0.")
        if not self.tracks:
            raise SchemaValidationError("AnimationClip.tracks cannot be empty.")

        track_names: set[str] = set()
        skeleton_names = set(skeleton.joint_names) if skeleton is not None else None

        for i, track in enumerate(self.tracks):
            track.validate(
                start_time=self.start_time,
                end_time=self.end_time,
                context=f"AnimationClip.tracks[{i}]",
            )
            name = track.joint_name
            if name in track_names:
                raise SchemaValidationError(
                    f"AnimationClip has duplicate track for joint {name!r}."
                )
            track_names.add(name)
            if skeleton_names is not None and name not in skeleton_names:
                raise SchemaValidationError(
                    f"AnimationClip track joint {name!r} is not part of SkeletonAsset {skeleton.name!r}."
                )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "name": self.name,
            "start_time": float(self.start_time),
            "end_time": float(self.end_time),
            "sample_rate_hz": float(self.sample_rate_hz),
            "tracks": [t.to_dict() for t in self.tracks],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AnimationClip":
        clip = cls(
            name=str(payload.get("name", "")),
            start_time=float(payload.get("start_time", 0.0)),
            end_time=float(payload.get("end_time", 0.0)),
            sample_rate_hz=float(payload.get("sample_rate_hz", 30.0)),
            tracks=[
                JointAnimationTrack.from_dict(dict(t))
                for t in list(payload.get("tracks") or [])
            ],
            metadata=dict(payload.get("metadata") or {}),
        )
        clip.validate()
        return clip

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end_time) - float(self.start_time))


__all__ = [
    "SchemaValidationError",
    "JointTransform",
    "Joint",
    "SkeletonAsset",
    "VertexInfluence",
    "VertexSkin",
    "SkeletalMeshAsset",
    "Vec3Keyframe",
    "QuatKeyframe",
    "JointAnimationTrack",
    "AnimationClip",
]
