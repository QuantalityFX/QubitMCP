from __future__ import annotations

from typing import Protocol

from .types import GeneratedMotion, GenerationPlan, MotionRequest


class MotionBackend(Protocol):
    def prepare(self, request: MotionRequest) -> GenerationPlan: ...
    def collect(self, plan: GenerationPlan) -> GeneratedMotion: ...
    def check(self) -> list[str]: ...


class MotionGenerationService:
    """The UI knows this API; repository imports and arguments belong to adapters."""

    def __init__(self, backend: MotionBackend):
        self.backend = backend

    def check(self) -> list[str]:
        return self.backend.check()

    def prepare(self, request: MotionRequest) -> GenerationPlan:
        request.validate()
        return self.backend.prepare(request)

    def collect(self, plan: GenerationPlan) -> GeneratedMotion:
        return self.backend.collect(plan)
