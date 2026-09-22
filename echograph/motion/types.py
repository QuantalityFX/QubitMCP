from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MotionRequest:
    prompt: str
    duration: float = 5.0
    seed: int = 42
    guidance: float = 2.5

    def validate(self) -> None:
        if not self.prompt.strip():
            raise ValueError("Enter a motion prompt.")
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("Duration must be a positive finite number.")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or not 0 <= self.seed < 2**32:
            raise ValueError("Seed must be an integer between 0 and 4294967295.")
        if not math.isfinite(self.guidance) or not 0 <= self.guidance <= 20:
            raise ValueError("Guidance must be between 0 and 20.")


@dataclass(frozen=True)
class GenerationPlan:
    command: tuple[str, ...]
    cwd: Path
    run_dir: Path
    environment: dict[str, str] = field(default_factory=dict)
    stages: tuple[GenerationPlan, ...] = ()


@dataclass
class GeneratedMotion:
    """An owned result, independent of model weights and upstream dependencies."""
    run_dir: Path
    archive_path: Path
    animation_path: Path
    bvh_path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
