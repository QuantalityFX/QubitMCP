from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import math
from pathlib import Path
from urllib.parse import urlsplit

RESOURCES = Path(__file__).with_name("resources")
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
LICENSE_URL = "https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE"


def loopback_url(url: str) -> str:
    parsed = urlsplit(url)
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
        port = parsed.port
    except (ValueError, TypeError):
        local, port = False, None
    if (parsed.scheme != "http" or not local or not port or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Use a local backend URL such as http://127.0.0.1:8188.")
    return url.rstrip("/")


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    model_variant: str = "int8"
    mode: str = "text_to_image"
    image: object = None
    reference_image: object = None
    seed: int | None = None
    steps: int = 40
    width: int = 1024
    height: int = 1024
    edit_resolution: int = 1024
    cfg: float = 1.0
    transparent: bool = False
    low_memory: bool = False
    backend: str = "comfyui"
    negative_prompt: str = ""
    reference_images: tuple = ()
    edit_guidance: str = "flexible"
    edit_framing: str = "auto"

    def validate(self):
        if self.backend not in ("comfyui", "native_diffusers"):
            raise ValueError("Choose an installed image backend.")
        variants = ("bf16",) if self.backend == "native_diffusers" else ("int8", "gguf_q4")
        if self.model_variant not in variants:
            raise ValueError("Native currently supports official BF16; INT8/GGUF need native validation."
                             if self.backend == "native_diffusers" else "Choose INT8 or GGUF Q4_K_M for ComfyUI.")
        if self.mode not in ("text_to_image", "image_edit"):
            raise ValueError("Choose Text to Image or Image Edit.")
        if not self.prompt.strip():
            raise ValueError("Enter an image description or an editing instruction.")
        if self.mode == "image_edit" and self.image is None:
            raise ValueError("Image Edit requires an input image or an image connection.")
        if self.mode != "image_edit" and (self.reference_image is not None or self.reference_images):
            raise ValueError("Reference images require Image Edit mode.")
        if self.backend == "comfyui" and self.reference_images:
            raise ValueError("Additional reference images require the native backend.")
        if self.backend == "comfyui" and self.negative_prompt:
            raise ValueError("Negative prompts are supported by the native backend only.")
        if self.edit_guidance not in ("flexible", "preserve_input", "pose_guide"):
            raise ValueError("Choose a supported edit guidance option.")
        if self.edit_framing not in ("auto", "input", "reference", "custom"):
            raise ValueError("Choose a supported edit framing option.")
        if self.backend == "comfyui" and (self.edit_guidance != "flexible" or self.edit_framing != "auto"):
            raise ValueError("Edit guidance and framing options require the native backend.")
        if self.mode == "image_edit" and self.edit_framing == "reference" and self.reference_image is None and not self.reference_images:
            raise ValueError("Match reference framing requires a reference image.")
        if 1 + int(self.reference_image is not None) + len(self.reference_images) > 10:
            raise ValueError("Use at most 10 ordered reference images, including the input image.")
        if not 1 <= self.steps <= 100:
            raise ValueError("Steps must be between 1 and 100.")
        if self.seed is not None and not 0 <= self.seed < 2**53:
            raise ValueError("Seed must be between 0 and 9007199254740991.")
        custom_edit = self.backend == "native_diffusers" and self.edit_framing == "custom"
        for name in (("edit_resolution",) if self.mode == "image_edit" and not custom_edit else ("width", "height", "edit_resolution")):
            number = getattr(self, name)
            if not 64 <= number <= 4096 or number % 32:
                raise ValueError(f"{name} must be 64..4096 and a multiple of 32.")
        if not math.isfinite(self.cfg) or not 0 <= self.cfg <= 20:
            raise ValueError("CFG must be between 0 and 20.")
        if self.backend == "native_diffusers" and self.cfg > 1 and not self.negative_prompt.strip():
            raise ValueError("Native CFG above 1 requires a negative prompt. Use CFG 1 for Qwen's recommended settings.")
        if self.backend == "native_diffusers" and self.cfg < 1:
            raise ValueError("Use CFG 1 for unguided native generation, or above 1 with a negative prompt.")
        return self

    @property
    def template(self):
        if self.mode == "image_edit":
            return "05-two-reference-edit.json" if self.reference_image is not None else "03-image-edit.json"
        return "04-transparent-rgba.json" if self.transparent else "01-text-to-image.json"
