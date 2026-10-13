"""Versioned disk caches with bounded in-memory preview storage."""
from collections import OrderedDict
import json
from pathlib import Path
import numpy as np


class FrameCache:
    def __init__(self, budget_mb=128):
        self.frames = OrderedDict()
        self.times = {}
        self.budget = int(budget_mb * 1024 * 1024)
        self.bytes = 0

    def put(self, frame, positions, velocity, *, simulation_time=None):
        frame = int(frame)
        value = (np.asarray(positions, dtype=np.float32).copy(), np.asarray(velocity, dtype=np.float32).copy())
        previous = self.frames.pop(frame, None)
        if previous is not None:
            self.bytes -= sum(a.nbytes for a in previous)
        self.frames[frame] = value
        if simulation_time is None:
            self.times.pop(frame, None)
        else:
            self.times[frame] = float(simulation_time)
        self.bytes += sum(a.nbytes for a in value)
        while self.bytes > self.budget and len(self.frames) > 1:
            removed_frame, removed = self.frames.popitem(last=False)
            self.times.pop(removed_frame, None)
            self.bytes -= sum(a.nbytes for a in removed)

    def get(self, frame):
        return self.frames.get(int(frame))

    def clear(self):
        self.frames.clear()
        self.times.clear()
        self.bytes = 0

    def truncate_after(self, frame):
        """Keep history up to the pose from which new settings start solving."""
        for key in list(self.frames):
            if key > frame:
                self.bytes -= sum(a.nbytes for a in self.frames.pop(key))
                self.times.pop(key, None)


def write_bake(path, metadata, frames, positions, velocities=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    values = dict(schema=np.array("qubit.cloth_cache.v2" if velocities is not None else "qubit.cloth_cache.v1"),
                  metadata=np.array(json.dumps(metadata, sort_keys=True)),
                  frames=np.asarray(frames, dtype=np.int32), positions=np.asarray(positions, dtype=np.float32))
    if velocities is not None:
        values["velocities"] = np.asarray(velocities, dtype=np.float32)
    try:
        with temporary.open("wb") as stream:
            np.savez_compressed(stream, **values)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def read_bake(path, expected_signature, *, include_velocities=False, geometry_signature=None,
              allow_legacy_state=False, placement_signature=None, legacy_geometry_signatures=()):
    with np.load(path, allow_pickle=False) as archive:
        if str(archive["schema"]) not in {"qubit.cloth_cache.v1", "qubit.cloth_cache.v2"}:
            raise ValueError("Unsupported cloth cache.")
        metadata = json.loads(str(archive["metadata"]))
        frames = archive["frames"].copy()
        positions = archive["positions"].copy()
        velocities = archive["velocities"].copy() if "velocities" in archive else np.zeros_like(positions)
    if frames.ndim != 1 or not len(frames) or np.any(np.diff(frames) <= 0):
        raise ValueError("Invalid cloth cache frame range.")
    if positions.ndim != 3 or positions.shape[0] != len(frames) or positions.shape[-1] != 3 or not np.isfinite(positions).all():
        raise ValueError("Invalid cloth cache positions.")
    if velocities.shape != positions.shape or not np.isfinite(velocities).all():
        raise ValueError("Invalid cloth cache velocities.")
    is_state = len(frames) == 1 and metadata.get("cache_type", "state") == "state"
    if is_state and placement_signature is not None and "placement_signature" in metadata:
        compatible = metadata["placement_signature"] == placement_signature
    elif geometry_signature is not None and "geometry_signature" in metadata:
        compatible = metadata["geometry_signature"] == geometry_signature
        if is_state and not compatible:
            compatible = metadata["geometry_signature"] in legacy_geometry_signatures
    else:
        compatible = metadata.get("signature") == expected_signature
        if (not compatible and allow_legacy_state and "geometry_signature" not in metadata
                and len(frames) == 1 and metadata.get("cache_type", "state") == "state"):
            # Legacy snapshots fingerprinted settings and had no topology header.
            # The viewport additionally checks their vertex count before applying.
            compatible = True
    if not compatible:
        raise ValueError("Cloth cache geometry differs from the pattern.")
    result = metadata, {int(f): p for f, p in zip(frames, positions)}
    if include_velocities:
        return (*result, {int(f): v for f, v in zip(frames, velocities)})
    return result
