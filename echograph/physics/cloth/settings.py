"""Validated, serializable solver controls. Distances are in meters."""
from dataclasses import asdict, dataclass, fields
import math


@dataclass(frozen=True)
class ClothSettings:
    enabled: bool = True
    stretch: float = 10000.0
    bend: float = 0.05
    seam_stiffness: float = 20000.0
    seam_seconds: float = 0.5
    seam_gap: float = 0.001
    gravity_enabled: bool = True
    gravity_y: float = -9.81
    damping: float = 0.5
    density: float = 0.2
    thickness: float = 0.003
    friction: float = 0.6
    substeps: int = 4
    iterations: int = 8
    collision_quality: int = 2
    self_collision: bool = True
    ground: bool = False
    ground_height: float = 0.0
    mesh_spacing_mm: float = 15.0
    fps: float = 24.0
    start_frame: int = 0
    end_frame: int = 120
    device: str = "auto"
    interactive: bool = False
    follow_timeline: bool = False
    scene_units_per_meter: float = 1.0

    def to_dict(self):
        return asdict(self)

    @property
    def effective_gravity_y(self):
        return self.gravity_y if self.gravity_enabled else 0.0

    @classmethod
    def from_dict(cls, values):
        defaults = cls()
        result = {}
        limits = {"stretch": (0, 1e9), "bend": (0, 1e7), "seam_stiffness": (0, 1e9),
                  "seam_seconds": (0, 60), "seam_gap": (0, 0.1), "gravity_y": (-1000, 1000),
                  "damping": (0, 100), "density": (0.001, 100), "thickness": (0.0001, 0.1),
                  "friction": (0, 1), "substeps": (1, 64), "iterations": (1, 64),
                  "collision_quality": (1, 8), "mesh_spacing_mm": (2, 100), "fps": (1, 240),
                  "start_frame": (0, 100000), "end_frame": (0, 100000),
                  "ground_height": (-1e6, 1e6), "scene_units_per_meter": (0.001, 10000)}
        for field in fields(cls):
            default = getattr(defaults, field.name)
            value = values.get(field.name, default)
            if isinstance(default, bool):
                value = str(value).strip().lower() in {"1", "true", "yes", "on"}
            elif isinstance(default, (float, int)):
                try:
                    value = float(value)
                    if not math.isfinite(value):
                        value = default
                except (TypeError, ValueError):
                    value = default
                low, high = limits[field.name]
                value = max(low, min(high, value))
                value = int(value) if isinstance(default, int) else float(value)
            elif field.name == "device":
                value = str(value).lower()
                if value not in {"auto", "cpu", "cuda"}:
                    value = "auto"
            result[field.name] = value
        result["end_frame"] = max(result["start_frame"], result["end_frame"])
        return cls(**result)
