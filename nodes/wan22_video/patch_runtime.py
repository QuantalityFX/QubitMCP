from __future__ import annotations

import argparse
import sys
from pathlib import Path


MARKER = "# QubitMCP Windows SDPA fallback"
INIT_MARKER = "# QubitMCP optional Wan imports"

HELPER = f'''

{MARKER}
def _qubitmcp_sdpa_attention(
    q,
    k,
    v,
    q_lens=None,
    k_lens=None,
    dropout_p=0.,
    softmax_scale=None,
    q_scale=None,
    causal=False,
    window_size=(-1, -1),
    deterministic=False,
    dtype=torch.bfloat16,
):
    half_dtypes = (torch.float16, torch.bfloat16)
    out_dtype = q.dtype
    if q_lens is not None or k_lens is not None:
        warnings.warn(
            'Flash attention is not available; using torch scaled_dot_product_attention without variable-length masking.'
        )
    if window_size != (-1, -1):
        warnings.warn(
            'Flash attention is not available; windowed attention is ignored by the torch SDPA fallback.'
        )
    if q_scale is not None:
        q = q * q_scale
    q = q if q.dtype in half_dtypes else q.to(dtype)
    k = k if k.dtype in half_dtypes else k.to(dtype)
    v = v if v.dtype in half_dtypes else v.to(dtype)
    q = q.transpose(1, 2)
    k = k.transpose(1, 2)
    v = v.transpose(1, 2)
    out = torch.nn.functional.scaled_dot_product_attention(
        q,
        k,
        v,
        attn_mask=None,
        dropout_p=dropout_p,
        is_causal=causal,
        scale=softmax_scale,
    )
    return out.transpose(1, 2).contiguous().type(out_dtype)
'''

INSERT_AFTER = """__all__ = [
    'flash_attention',
    'attention',
]
"""

FALLBACK_GUARD = """    if not (FLASH_ATTN_2_AVAILABLE or FLASH_ATTN_3_AVAILABLE):
        return _qubitmcp_sdpa_attention(
            q=q,
            k=k,
            v=v,
            q_lens=q_lens,
            k_lens=k_lens,
            dropout_p=dropout_p,
            softmax_scale=softmax_scale,
            q_scale=q_scale,
            causal=causal,
            window_size=window_size,
            deterministic=deterministic,
            dtype=dtype,
        )

"""

FUNCTION_START = """def flash_attention(
    q,
    k,
    v,
    q_lens=None,
    k_lens=None,
    dropout_p=0.,
    softmax_scale=None,
    q_scale=None,
    causal=False,
    window_size=(-1, -1),
    deterministic=False,
    dtype=torch.bfloat16,
    version=None,
):
"""

PATCHED_INIT = f"""# Copyright 2024-2025 The Alibaba Wan Team Authors. All rights reserved.
from . import configs, distributed, modules
from .image2video import WanI2V
from .text2video import WanT2V
from .textimage2video import WanTI2V

{INIT_MARKER}
def _optional_import(module_name, attr_name):
    try:
        module = __import__(f"{{__name__}}.{{module_name}}", fromlist=[attr_name])
        return getattr(module, attr_name)
    except ModuleNotFoundError as exc:
        return None


WanS2V = _optional_import("speech2video", "WanS2V")
WanAnimate = _optional_import("animate", "WanAnimate")
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch Wan2.2 runtime for Windows-friendly attention fallback.")
    parser.add_argument("--wan-root", required=True, help="Path to the Wan2.2 checkout.")
    return parser.parse_args(argv)


def patch_attention(wan_root: Path) -> bool:
    path = wan_root / "wan" / "modules" / "attention.py"
    if not path.exists():
        raise FileNotFoundError(f"Wan attention.py was not found: {path}")
    text = path.read_text(encoding="utf-8")
    changed = False
    if MARKER not in text:
        if INSERT_AFTER not in text:
            raise RuntimeError("Could not find Wan attention __all__ block to patch.")
        text = text.replace(INSERT_AFTER, INSERT_AFTER + HELPER, 1)
        changed = True
    if FALLBACK_GUARD.strip() not in text:
        if FUNCTION_START not in text:
            raise RuntimeError("Could not find Wan flash_attention function to patch.")
        text = text.replace(FUNCTION_START, FUNCTION_START + FALLBACK_GUARD, 1)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8", newline="\n")
    return changed


def patch_optional_imports(wan_root: Path) -> bool:
    path = wan_root / "wan" / "__init__.py"
    if not path.exists():
        raise FileNotFoundError(f"Wan __init__.py was not found: {path}")
    text = path.read_text(encoding="utf-8")
    if text == PATCHED_INIT:
        return False
    path.write_text(PATCHED_INIT, encoding="utf-8", newline="\n")
    return True


def patch_generate_controls(wan_root: Path) -> bool:
    path = wan_root / "generate.py"
    if not path.exists():
        raise FileNotFoundError(f"Wan generate.py was not found: {path}")
    text = path.read_text(encoding="utf-8")
    changed = False
    if '"--sample_neg_prompt"' not in text:
        needle = '''    parser.add_argument(
        "--sample_guide_scale",
        type=float,
        default=None,
        help="Classifier free guidance scale.")
'''
        insert = '''    parser.add_argument(
        "--sample_neg_prompt",
        type=str,
        default="",
        help="Negative prompt override. If empty, use the model default.")
'''
        if needle not in text:
            raise RuntimeError("Could not find Wan sample_guide_scale parser block to patch.")
        text = text.replace(needle, needle + insert, 1)
        changed = True
    legacy_image_change_arg = '''    parser.add_argument(
        "--image_change",
        type=float,
        default=1.0,
        help="Image-to-video change strength. 0 stays closest to the input image; 1 uses normal Wan behavior.")
'''
    if legacy_image_change_arg in text:
        text = text.replace(legacy_image_change_arg, "", 1)
        changed = True
    if "def _sample_neg_prompt(cfg, extra: str) -> str:" not in text:
        needle = '''

def _parse_args():
'''
        insert = '''

def _sample_neg_prompt(cfg, extra: str) -> str:
    base = str(getattr(cfg, "sample_neg_prompt", "") or "").strip()
    custom = str(extra or "").strip()
    if base and custom:
        return f"{base}, {custom}"
    return custom or base
'''
        if needle not in text:
            raise RuntimeError("Could not find Wan parse-args marker to patch negative prompt helper.")
        text = text.replace(needle, insert + needle, 1)
        changed = True
    head, sep, tail = text.partition('    elif "ti2v" in args.task:')
    if not sep:
        raise RuntimeError("Could not find Wan TI2V generate branch to patch.")
    branch, branch_sep, branch_rest = tail.partition('    elif "animate" in args.task:')
    if "image_change=args.image_change" in branch:
        branch = branch.replace("            image_change=args.image_change,\n", "", 1)
        text = head + sep + branch + branch_sep + branch_rest
        changed = True
    if 'n_prompt=args.sample_neg_prompt or ""' in branch:
        branch = branch.replace('n_prompt=args.sample_neg_prompt or ""', "n_prompt=_sample_neg_prompt(cfg, args.sample_neg_prompt)", 1)
        text = head + sep + branch + branch_sep + branch_rest
        changed = True
    if "n_prompt=_sample_neg_prompt(cfg, args.sample_neg_prompt)" not in branch:
        needle = '''            guide_scale=args.sample_guide_scale,
            seed=args.base_seed,
            offload_model=args.offload_model)
'''
        replacement = '''            guide_scale=args.sample_guide_scale,
            n_prompt=_sample_neg_prompt(cfg, args.sample_neg_prompt),
            seed=args.base_seed,
            offload_model=args.offload_model)
'''
        if needle not in branch:
            raise RuntimeError("Could not find Wan TI2V generate call to patch.")
        branch = branch.replace(needle, replacement, 1)
        text = head + sep + branch + branch_sep + branch_rest
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8", newline="\n")
    return changed


def cleanup_ti2v_image_change(wan_root: Path) -> bool:
    path = wan_root / "wan" / "textimage2video.py"
    if not path.exists():
        raise FileNotFoundError(f"Wan textimage2video.py was not found: {path}")
    text = path.read_text(encoding="utf-8")
    original = text
    replacements = (
        (
            '''                 n_prompt="",
                 seed=-1,
                 offload_model=True,
                 image_change=1.0):
''',
            '''                 n_prompt="",
                 seed=-1,
                 offload_model=True):
''',
        ),
        (
            '''            n_prompt="",
            seed=-1,
            offload_model=True,
            image_change=1.0):
''',
            '''            n_prompt="",
            seed=-1,
            offload_model=True):
''',
        ),
        (
            '''                seed=seed,
                offload_model=offload_model,
                image_change=image_change)
''',
            '''                seed=seed,
                offload_model=offload_model)
''',
        ),
        (
            '''            image_change (`float`, *optional*, defaults to 1.0):
                Image-to-video change strength. 0 stays closest to the input
                image; 1 uses normal Wan behavior.
''',
            "",
        ),
        (
            '''            # sample videos
            latent = noise
            mask1, mask2 = masks_like([noise], zero=True)
            # QubitMCP image-change latent mask
            ref_mask = mask2[0]
            try:
                image_change = max(0.0, min(1.0, float(image_change)))
            except Exception:
                image_change = 1.0
            if image_change < 0.999:
                ref_mask = torch.minimum(ref_mask, torch.full_like(ref_mask, image_change))
            latent = (1. - ref_mask) * z[0] + ref_mask * latent
''',
            '''            # sample videos
            latent = noise
            mask1, mask2 = masks_like([noise], zero=True)
            latent = (1. - mask2[0]) * z[0] + mask2[0] * latent
''',
        ),
        (
            "                temp_ts = (ref_mask[0][:, ::2, ::2] * timestep).flatten()\n",
            "                temp_ts = (mask2[0][0][:, ::2, ::2] * timestep).flatten()\n",
        ),
        (
            "                latent = (1. - ref_mask) * z[0] + ref_mask * latent\n",
            "                latent = (1. - mask2[0]) * z[0] + mask2[0] * latent\n",
        ),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8", newline="\n")
        return True
    return False


def patch_ti2v_dtype_load(wan_root: Path) -> bool:
    path = wan_root / "wan" / "textimage2video.py"
    if not path.exists():
        raise FileNotFoundError(f"Wan textimage2video.py was not found: {path}")
    text = path.read_text(encoding="utf-8")
    old = '''        logging.info(f"Creating WanModel from {checkpoint_dir}")
        self.model = WanModel.from_pretrained(checkpoint_dir)
        self.model = self._configure_model(
            model=self.model,
            use_sp=use_sp,
            dit_fsdp=dit_fsdp,
            shard_fn=shard_fn,
            convert_model_dtype=convert_model_dtype)
'''
    new = '''        logging.info(f"Creating WanModel from {checkpoint_dir}")
        if convert_model_dtype:
            self.model = WanModel.from_pretrained(checkpoint_dir, torch_dtype=self.param_dtype)
        else:
            self.model = WanModel.from_pretrained(checkpoint_dir)
        self.model = self._configure_model(
            model=self.model,
            use_sp=use_sp,
            dit_fsdp=dit_fsdp,
            shard_fn=shard_fn,
            convert_model_dtype=False)
'''
    if old not in text:
        return False
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    wan_root = Path(args.wan_root).expanduser()
    changed = patch_attention(wan_root)
    changed = patch_optional_imports(wan_root) or changed
    changed = patch_generate_controls(wan_root) or changed
    changed = cleanup_ti2v_image_change(wan_root) or changed
    changed = patch_ti2v_dtype_load(wan_root) or changed
    if changed:
        print(f"[wan2.2] Patched Wan runtime: {wan_root}")
    else:
        print(f"[wan2.2] Wan runtime already patched: {wan_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
