"""Run only under HandMDM's isolated interpreter. No renderer or dataset preload."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def generate(request_path: Path):
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    output = request_path.parent / "hands.npz"
    partial = output.with_suffix(".partial.npz")
    if output.exists() or partial.exists():
        raise FileExistsError("Hand output already exists; start a new generation.")
    os.chdir(payload["repository"])
    sys.path.insert(0, payload["repository"])
    import numpy as np
    import torch
    import pytorch_lightning as pl
    from hydra.utils import instantiate
    from omegaconf import OmegaConf
    from src.data.collate import collate_tensor_with_padding, length_to_mask

    device = torch.device(payload["device"])
    if device.type == "cuda" and (not torch.cuda.is_available() or device.index >= torch.cuda.device_count()):
        raise RuntimeError(f"HandMDM device {device} is unavailable; select CPU or install CUDA PyTorch later.")
    request = payload["request"]
    pl.seed_everything(request["seed"])
    config = OmegaConf.create(json.loads(Path(payload["config"]).read_text(encoding="utf-8")))
    config.run_dir = str(Path(payload["config"]).parent)
    config.data.text_encoder.device = str(device)
    # Unlike stock inference.py, on-demand encoding needs no annotation arrays.
    config.data.text_encoder.preload = False
    config.data.text_encoder.no_model = False
    print("Loading HandMDM and CLIP text encoder…", flush=True)
    checkpoint = torch.load(payload["checkpoint"], map_location="cpu", weights_only=False)
    diffusion = instantiate(config.diffusion)
    diffusion.load_state_dict(checkpoint["state_dict"], strict=True)
    diffusion.eval().to(device)
    text_encoder = instantiate(config.data.text_encoder)

    def encode(text):
        embedding = text_encoder(text)
        if isinstance(embedding, torch.Tensor):
            values = embedding[0] if embedding.dim() > 1 else embedding
            if values.dim() == 1:
                values = values.unsqueeze(0)
            embedding = {"x": values, "length": len(values)}
        lengths = torch.tensor([embedding["length"]], device=device)
        return {"x": collate_tensor_with_padding([embedding["x"]]).to(device),
                "length": lengths, "mask": length_to_mask(lengths, device=device)}

    with torch.inference_mode():
        conditioned, unconditioned = encode(request["prompt"]), encode("")
        infos = {"all_lengths": torch.tensor([request["frames"]], device=device),
                 "all_texts": [request["prompt"]], "guidance_weight": request["guidance"]}
        print(f"Sampling {request['frames']} hand frames at 25 fps…", flush=True)
        # Released GaussianDiffusion.forward also applies the trained inverse normalizer.
        features = diffusion(conditioned, unconditioned, infos, progress_bar=None)
        features = features[0, :request["frames"]].detach().cpu().numpy()
    if features.shape != (request["frames"], 274) or not np.isfinite(features).all():
        raise ValueError("HandMDM produced invalid motion features.")
    metadata = {**payload, "torch_version": torch.__version__, "python_version": sys.version,
                "rotation_convention": "SMPL-X local axis angles = negative of decoded row-6D axis angles",
                "text_encoding": "on demand; no dataset preload", "config_snapshot": OmegaConf.to_container(config, resolve=True)}
    with partial.open("xb") as stream:
        np.savez_compressed(stream, features=features, fps=25.0,
                            metadata=np.array(json.dumps(metadata, ensure_ascii=False)))
    partial.rename(output)
    print(f"Saved {output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    generate(parser.parse_args().request.resolve())
