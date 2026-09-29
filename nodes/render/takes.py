"""Take naming and render outcomes shared by manual and For Each renders."""
from dataclasses import dataclass, asdict
from pathlib import Path
import re


def take_name(number: int, source_path: str = "") -> str:
    label = Path(source_path).parent.name if source_path else ""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")[:100]
    return f"Take_{number:03d}" + (f"_{label}" if label else "")


def reserve_take_folder(parent: Path, number: int, source_path: str = "") -> Path:
    """Never overwrite a previous take, even when the same batch is run twice."""
    parent.mkdir(parents=True, exist_ok=True)
    name = take_name(number, source_path)
    for suffix in range(100000):
        folder = parent / (name if suffix == 0 else f"{name}_{suffix + 1:02d}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise OSError("Could not allocate a unique take folder.")


@dataclass
class RenderResult:
    status: str
    message: str = ""
    output_template: str = ""
    written_frames: int = 0
    start_frame: int = 0
    end_frame: int = 0

    def to_dict(self):
        return asdict(self)
