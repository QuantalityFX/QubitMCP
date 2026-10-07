"""Readable, owner-qualified joint references for Scene constraint targets."""
from __future__ import annotations


def joint_target_name(owner: str, joint: str) -> str:
    owner, joint = str(owner or "").strip(), str(joint or "").strip()
    return f"{owner}/Skeleton/{joint}" if owner and joint else ""


def split_joint_target(text: str) -> tuple[str, str] | None:
    text = str(text or "").strip()
    for marker in ("/skeleton/", "::skeleton_joint::"):
        index = text.lower().find(marker)
        if index >= 0:
            owner, joint = text[:index].strip(), text[index + len(marker):].strip()
            return (owner, joint) if owner and joint else None
    return None


def resolve_joint_target(text: str, skeletons) -> tuple[str, int] | None:
    """Resolve a qualified reference or a joint name unique among these rigs."""
    text = str(text or "").strip()
    if not text:
        return None
    qualified = split_joint_target(text)
    joint_name = qualified[1] if qualified else text
    matches = []
    for owner, skeleton in skeletons:
        if qualified and str(owner).casefold() != qualified[0].casefold():
            continue
        for index, joint in enumerate(getattr(skeleton, "joints", []) or []):
            if str(getattr(joint, "name", "")).strip().casefold() == joint_name.casefold():
                matches.append((owner, index))
    return matches[0] if len(matches) == 1 else None
