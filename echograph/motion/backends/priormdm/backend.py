from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import uuid

from ...types import GeneratedMotion, GenerationPlan, MotionRequest

PROJECT_ROOT = Path(__file__).resolve().parents[4]
UPSTREAM_URL = "https://github.com/priorMDM/priorMDM.git"
INSPECTED_REVISION = "176fbee62a4d62ae007399138bfc3b1722b58715"
FPS = 20.0
MAX_FRAMES = 196


@dataclass(frozen=True)
class PriorMDMConfig:
    repository: Path = PROJECT_ROOT / "third_party" / "PriorMDM"
    python: Path = PROJECT_ROOT / "third_party" / "PriorMDM" / ".venv" / "Scripts" / "python.exe"
    checkpoint: Path | None = None
    dataset: Path = PROJECT_ROOT / "third_party" / "PriorMDM" / "dataset" / "HumanML3D"
    output_root: Path = PROJECT_ROOT / "logs" / "motion" / "priormdm"
    device: str = "cuda:0"
    download_cache: Path | None = None

    @property
    def cache_directory(self) -> Path:
        return self.download_cache or self.repository / "downloads"


def checkpoint_settings(path: Path) -> dict:
    settings = json.loads(path.with_name("args.json").read_text(encoding="utf-8"))
    if not isinstance(settings, dict) or settings.get("dataset") != "humanml":
        raise ValueError("Choose a base HumanML3D checkpoint with its original args.json.")
    if settings.get("inpainting_mask") or settings.get("multi_train_mode") or settings.get("trans_emb"):
        raise ValueError("This node supports base text-to-motion checkpoints; controlled and two-person models need separate adapters.")
    steps = settings.get("diffusion_steps")
    if not isinstance(steps, int) or isinstance(steps, bool) or steps not in (50, 1000):
        raise ValueError("args.json must explicitly specify diffusion_steps as 50 or 1000; do not relabel a checkpoint.")
    return settings


class PriorMDMBackend:
    def __init__(self, config: PriorMDMConfig, *, preview_profile="model"):
        self.config = config
        if preview_profile not in ("model", "standardman"):
            raise ValueError("Unknown preview proportions.")
        self.preview_profile = preview_profile

    def check(self) -> list[str]:
        cfg = self.config
        errors = []
        required = [(cfg.python, "Isolated Python environment"),
                    (cfg.repository / "utils" / "model_util.py", "PriorMDM source"),
                    (cfg.dataset / "Mean.npy", "HumanML3D training Mean.npy"),
                    (cfg.dataset / "Std.npy", "HumanML3D training Std.npy"),
                    (cfg.repository / "body_models" / "smpl" / "SMPL_NEUTRAL.pkl", "SMPL body model"),
                    (cfg.repository / "body_models" / "smpl" / "J_regressor_extra.npy", "SMPL joint regressor")]
        for path, label in required:
            if not path.is_file():
                errors.append(f"{label} missing: {path}")
        if cfg.checkpoint is None or not cfg.checkpoint.is_file():
            errors.append("Base HumanML3D checkpoint missing. Use Setup / dependencies to install and select it automatically.")
        else:
            try:
                checkpoint_settings(cfg.checkpoint)
            except (OSError, ValueError) as exc:
                errors.append(f"Checkpoint configuration: {exc}")
        if not re.fullmatch(r"cpu|cuda:\d+", cfg.device):
            errors.append("Device must be cpu or cuda:N (for example cuda:0).")
        return errors

    def prepare(self, request: MotionRequest) -> GenerationPlan:
        request.validate()
        if not 0.1 <= request.duration <= MAX_FRAMES / FPS:
            raise ValueError("Base HumanML3D generation supports 0.1–9.8 seconds at 20 fps. Longer sequences require DoubleTake.")
        errors = self.check()
        if errors:
            raise ValueError("\n".join(errors))
        cfg = self.config
        settings = checkpoint_settings(cfg.checkpoint)
        if settings.get("cond_mask_prob", 0.1) == 0 and request.guidance != 1:
            raise ValueError("This checkpoint has no classifier-free guidance; use guidance 1.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = (cfg.output_root / f"{stamp}_{uuid.uuid4().hex[:12]}").resolve()
        # Never pass an existing user directory to upstream sampling code.
        run_dir.mkdir(parents=True, exist_ok=False)
        try:
            revision = subprocess.check_output(
                ["git", "-C", str(cfg.repository), "rev-parse", "HEAD"],
                text=True, stderr=subprocess.DEVNULL, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).strip()
        except (OSError, subprocess.SubprocessError):
            revision = "unknown"
        payload = {"schema_version": 1, "backend": "priormdm", "request": asdict(request),
                   "repository": str(cfg.repository.resolve()), "checkpoint": str(cfg.checkpoint.resolve()),
                   "dataset": str(cfg.dataset.resolve()), "device": cfg.device,
                   "frames": int(round(request.duration * FPS)), "fps": FPS,
                   "diffusion_steps": settings["diffusion_steps"], "revision": revision,
                   "preview_profile": self.preview_profile}
        request_file = run_dir / "request.json"
        request_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        worker = Path(__file__).with_name("worker.py")
        return GenerationPlan(
            command=(str(cfg.python.resolve()), "-u", str(worker), "--request", str(request_file)),
            cwd=cfg.repository.resolve(), run_dir=run_dir,
            environment={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONNOUSERSITE": "1"},
        )

    def collect_animation(self, plan: GenerationPlan):
        from ...conversion import save_generated_animation
        return save_generated_animation(plan.run_dir, self.preview_profile)

    def collect(self, plan: GenerationPlan) -> GeneratedMotion:
        from ...conversion import save_generated_motion
        return save_generated_motion(plan.run_dir, self.preview_profile)
