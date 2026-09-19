"""Load canonical saved animation without a model checkout or ML dependencies."""
from __future__ import annotations

import json
from pathlib import Path

from echograph.rigging.fbx_canonical import AnimationClip, SkeletonAsset


def load_animation(path: str | Path) -> tuple[SkeletonAsset, AnimationClip]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported motion animation schema version.")
    skeleton = SkeletonAsset.from_dict(payload["skeleton"])
    clip = AnimationClip.from_dict(payload["clip"])
    clip.validate(skeleton)
    return skeleton, clip
