from __future__ import annotations


def scene_xform_is_identity(xf) -> bool:
    if not isinstance(xf, dict):
        return True
    try:
        pos = xf.get("pos", (0.0, 0.0, 0.0))
        rot = xf.get("rot", (0.0, 0.0, 0.0))
        scl = xf.get("scl", (1.0, 1.0, 1.0))
        return (
            all(abs(float(v)) < 1e-6 for v in (pos or (0.0, 0.0, 0.0)))
            and all(abs(float(v)) < 1e-6 for v in (rot or (0.0, 0.0, 0.0)))
            and all(abs(float(v) - 1.0) < 1e-6 for v in (scl or (1.0, 1.0, 1.0)))
        )
    except Exception:
        return False


def frame_refresh_xform_seed(frame: bool, incoming, previous):
    if (
        (not bool(frame))
        and scene_xform_is_identity(incoming)
        and isinstance(previous, dict)
        and not scene_xform_is_identity(previous)
    ):
        return dict(previous)
    return None
