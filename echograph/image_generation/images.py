"""Qubit-authored image normalization and results shared by inference providers."""
from dataclasses import dataclass
import io
from pathlib import Path
import threading
from typing import Protocol
from PIL import Image, ImageOps
from .schema import ImageRequest, MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS


class Cancelled(RuntimeError):
    pass


@dataclass
class ImageResult:
    image: Image.Image
    image_path: Path
    seed_used: int
    job_id: str
    metadata: dict


class ImageGenerationBackend(Protocol):
    def generate(self, request: ImageRequest, output_dir: Path, cancel: threading.Event,
                 progress) -> ImageResult: ...


def normalize_image(source) -> Image.Image:
    """Accept Pillow, Qt images, PNG bytes, host image dictionaries, or disk paths."""
    if isinstance(source, dict):
        source = next((source[key] for key in ("image", "image_path", "path")
                       if source.get(key) is not None), None)
    if isinstance(source, Image.Image):
        if source.width * source.height > MAX_IMAGE_PIXELS:
            raise ValueError("Input image exceeds 16 megapixels.")
        image = source.copy()
    elif isinstance(source, (bytes, bytearray)):
        if len(source) > MAX_IMAGE_BYTES:
            raise ValueError("Input image exceeds 64 MB.")
        with Image.open(io.BytesIO(source)) as original:
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise ValueError("Input image exceeds 16 megapixels.")
            image = original.copy()
    elif isinstance(source, (str, Path)):
        path = Path(source).expanduser()
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Input image exceeds 64 MB.")
        with Image.open(path) as original:
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise ValueError("Input image exceeds 16 megapixels.")
            image = ImageOps.exif_transpose(original).copy()
    else:
        # Imported only for host images; headless clients do not require Qt.
        from PySide6 import QtCore, QtGui
        if not isinstance(source, (QtGui.QImage, QtGui.QPixmap)) or source.isNull():
            raise ValueError("The input is not a supported image.")
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        source.save(buffer, "PNG")
        return normalize_image(bytes(buffer.data()))
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError("Input image exceeds 16 megapixels.")
    return image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")


