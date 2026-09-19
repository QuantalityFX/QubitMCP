"""Backend-independent motion generation and animation interchange.

Importing this package never imports PyTorch or an external model repository.
"""

from .api import MotionGenerationService
from .types import MotionRequest, GeneratedMotion

__all__ = ["MotionGenerationService", "MotionRequest", "GeneratedMotion"]
