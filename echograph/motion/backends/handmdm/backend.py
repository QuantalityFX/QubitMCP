"""HandMDM file/process boundary. Importing this module never loads ML packages."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import uuid

from ...types import GenerationPlan

ROOT = Path(__file__).resolve().parents[4]
REPOSITORY = ROOT / "third_party/HandMDM"


@dataclass(frozen=True)
class HandMDMConfig:
    repository: Path = REPOSITORY
    python: Path = REPOSITORY / ".venv/Scripts/python.exe"
    checkpoint: Path = REPOSITORY / "models/mdm_bobsl3dt_phonology_hms/checkpoints/last.ckpt"
    output_root: Path = ROOT / "logs/motion/handmdm"
    device: str = "cuda:0"


@dataclass(frozen=True)
class HandMotionRequest:
    prompt: str
    seed: int = 42
    guidance: float = 15.0
    frames: int = 14

    def validate(self):
        if not self.prompt.strip():
            raise ValueError("Enter a hand description or choose a supported gesture.")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or not 0 <= self.seed < 2**32:
            raise ValueError("Hand seed must be an integer from 0 to 4294967295.")
        if not math.isfinite(self.guidance) or not 0 <= self.guidance <= 30:
            raise ValueError("Hand guidance must be between 0 and 30.")
        if isinstance(self.frames, bool) or not isinstance(self.frames, int) or not 2 <= self.frames <= 14:
            raise ValueError("Use 2–14 native hand frames; longer clips need a separate continuation model.")


def find_run_config(checkpoint: Path, repository: Path) -> Path:
    """Keep config discovery inside this checkout, not unrelated ancestor dirs."""
    checkpoint, repository = checkpoint.resolve(), repository.resolve()
    # External checkpoint folders are valid, but only its folder and parent
    # are considered (the released layout is <run>/checkpoints/last.ckpt).
    candidates = [checkpoint.parent, checkpoint.parent.parent]
    for folder in candidates:
        candidate = folder / "config.json"
        if candidate.is_file():
            return candidate
    raise ValueError("HandMDM checkpoint needs its original run folder with config.json.")


class HandMDMBackend:
    def __init__(self, config: HandMDMConfig):
        self.config = config

    def check(self) -> list[str]:
        cfg = self.config
        errors = []
        for path, label in ((cfg.repository / "src/model/gaussian.py", "HandMDM source"),
                            (cfg.python, "HandMDM inference Python"),
                            (cfg.checkpoint, "HandMDM checkpoint")):
            if not path.is_file():
                errors.append(f"{label} missing: {path}")
        if cfg.checkpoint.is_file():
            try:
                config = json.loads(find_run_config(cfg.checkpoint, cfg.repository).read_text(encoding="utf-8"))
                if config["diffusion"]["denoiser"]["nfeats"] != 274:
                    raise ValueError("This adapter requires the 274-feature SMPL-X HandMDM checkpoint.")
                for key in ("motion_normalizer", "text_normalizer"):
                    normalizer = config["diffusion"][key]
                    if not normalizer.get("disable", False):
                        for name in ("mean.pt", "std.pt"):
                            path = cfg.repository / normalizer["base_dir"] / name
                            if not path.is_file():
                                errors.append(f"HandMDM {key} missing: {path}")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"HandMDM configuration: {exc}")
        if not re.fullmatch(r"cpu|cuda:\d+", cfg.device):
            errors.append("Hand device must be cpu or cuda:N.")
        if errors:
            errors.append("Use the node's HandMDM setup button when you are ready to install.")
        return errors

    def prepare(self, request: HandMotionRequest) -> GenerationPlan:
        request.validate()
        errors = self.check()
        if errors:
            raise ValueError("\n".join(errors))
        cfg = self.config
        run_dir = (cfg.output_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                     + "_" + uuid.uuid4().hex[:12])).resolve()
        run_dir.mkdir(parents=True, exist_ok=False)
        try:
            revision = subprocess.check_output(
                ["git", "-C", str(cfg.repository), "rev-parse", "HEAD"],
                text=True, stderr=subprocess.DEVNULL, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()
        except (OSError, subprocess.SubprocessError):
            revision = "unknown"
        payload = {"schema_version": 1, "backend": "handmdm", "request": asdict(request),
                   "repository": str(cfg.repository.resolve()), "checkpoint": str(cfg.checkpoint.resolve()),
                   "config": str(find_run_config(cfg.checkpoint, cfg.repository)),
                   "device": cfg.device, "fps": 25.0, "revision": revision}
        request_path = run_dir / "request.json"
        request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return GenerationPlan((str(cfg.python.resolve()), "-u", str(Path(__file__).with_name("worker.py")),
                               "--request", str(request_path)), cfg.repository.resolve(), run_dir,
                              {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1"})

    def collect(self, plan: GenerationPlan):
        from ...hands import load_hand_archive
        return load_hand_archive(plan.run_dir / "hands.npz")
