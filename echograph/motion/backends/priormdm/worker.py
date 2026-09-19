"""Standalone worker: executed ONLY with the isolated PriorMDM interpreter.

Uses the upstream parser, checkpoint loader, sampler and HumanML decoder without
patching the checkout. The dataset placeholder supplies num_actions only (see
utils.model_util.get_model_args); explicit prompts need normalization statistics,
not the training dataset, GloVe, evaluators or the dataset's random text loader.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def probe(repository: Path) -> None:
    os.chdir(repository)
    sys.path.insert(0, str(repository))
    import torch
    from utils.model_util import load_model  # noqa: F401: check transitive imports
    from data_loaders.humanml.scripts.motion_process import recover_from_ric  # noqa: F401
    print(json.dumps({"python": sys.version, "torch": torch.__version__,
                      "cuda_available": torch.cuda.is_available(),
                      "cuda_devices": torch.cuda.device_count()}), flush=True)


def generate(request_path: Path) -> None:
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    target = request_path.parent / "motion.npz"
    temporary = target.with_suffix(".partial.npz")
    legacy = request_path.parent / "results.npy"
    if any(path.exists() for path in (target, temporary, legacy)):
        raise FileExistsError("This run already contains output; start a new generation.")
    repository = Path(payload["repository"])
    os.chdir(repository)
    sys.path.insert(0, str(repository))
    import numpy as np
    import torch
    from utils.fixseed import fixseed
    from utils import parser_util
    from utils.model_util import load_model
    from data_loaders.humanml.scripts.motion_process import recover_from_ric

    request = payload["request"]
    device = torch.device(payload["device"])
    if device.type == "cuda" and (not torch.cuda.is_available() or device.index >= torch.cuda.device_count()):
        raise RuntimeError(f"{device} is unavailable in this environment. Install a compatible CUDA PyTorch build or select cpu.")
    fixseed(request["seed"])
    # Use upstream defaults and its args.json override logic, with explicit argv.
    parser = argparse.ArgumentParser()
    for add_options in (parser_util.add_base_options, parser_util.add_sampling_options,
                        parser_util.add_data_options, parser_util.add_model_options,
                        parser_util.add_diffusion_options):
        add_options(parser)
    args = parser.parse_args(["--model_path", payload["checkpoint"],
                              "--guidance_param", str(request["guidance"])])
    args = parser_util.load_from_model(args, parser)
    if args.dataset != "humanml" or args.diffusion_steps != payload["diffusion_steps"]:
        raise ValueError("Checkpoint settings changed after the request was prepared.")
    args.batch_size = 1
    dataset = Path(payload["dataset"])
    mean = np.load(dataset / "Mean.npy", allow_pickle=False)
    std = np.load(dataset / "Std.npy", allow_pickle=False)
    if mean.shape != (263,) or std.shape != (263,) or not np.isfinite(mean).all() or not np.isfinite(std).all() or (std <= 0).any():
        raise ValueError("HumanML3D Mean.npy and Std.npy must be finite 263-element training statistics with positive standard deviations.")
    print(f"Loading base HumanML3D checkpoint ({args.diffusion_steps} diffusion steps) on {device}...", flush=True)
    model, diffusion = load_model(args, SimpleNamespace(dataset=SimpleNamespace(num_actions=1)), device)
    frames = int(payload["frames"])
    condition = {"text": [request["prompt"]], "lengths": torch.tensor([frames], device=device),
                 "mask": torch.ones((1, 1, 1, frames), device=device, dtype=torch.bool),
                 "scale": torch.tensor([args.guidance_param], device=device)}
    print(f"Sampling {frames} frames at {payload['fps']:g} fps...", flush=True)
    with torch.no_grad():
        sample = diffusion.p_sample_loop(
            model, (1, model.njoints, model.nfeats, frames), clip_denoised=False,
            model_kwargs={"y": condition}, skip_timesteps=0, init_image=None,
            progress=True, dump_steps=None, noise=None, const_noise=False,
        )
        features = sample.cpu().permute(0, 2, 3, 1).float()
        features = features * torch.from_numpy(std).float() + torch.from_numpy(mean).float()
        joints = recover_from_ric(features, 22)[0, 0].numpy()
    metadata = {**payload, "torch_version": torch.__version__, "python_version": sys.version,
                "coordinate_system": "right-handed Y-up, metres; native HumanML3D",
                "joint_count": 22, "rotation_source": "reconstructed from XYZ for preview",
                "contact_order": ["left_ankle", "left_foot", "right_ankle", "right_foot"]}
    # A non-pickled archive is the application boundary. Keep all 263 original
    # denormalized features for future rotation/contact/retargeting improvements.
    with temporary.open("xb") as stream:
        np.savez_compressed(stream, positions=joints, fps=np.array(payload["fps"]),
                            features=features[0, 0].numpy(), contacts=features[0, 0, :, -4:].numpy(),
                            metadata=np.array(json.dumps(metadata, ensure_ascii=False)))
    temporary.rename(target)
    # Also preserve the documented upstream interchange layout for research tools.
    with legacy.open("xb") as stream:
        np.save(stream, {
            "motion": joints.transpose(1, 2, 0)[None], "text": [request["prompt"]],
            "lengths": np.array([frames]), "num_samples": 1, "num_repetitions": 1,
        })
    print(f"Saved {target}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--probe", type=Path, metavar="REPOSITORY")
    args = parser.parse_args()
    if args.probe:
        probe(args.probe.resolve())
    elif args.request:
        generate(args.request.resolve())
    else:
        parser.error("Provide --request or --probe.")


if __name__ == "__main__":
    main()
