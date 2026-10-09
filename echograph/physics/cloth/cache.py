"""Versioned disk caches with bounded in-memory preview storage."""
from collections import OrderedDict
import json
from pathlib import Path
import numpy as np


class FrameCache:
    def __init__(self, budget_mb=128):
        self.frames = OrderedDict()
        self.budget = int(budget_mb * 1024 * 1024)
        self.bytes = 0

    def put(self, frame, positions, velocity):
        value = (np.asarray(positions, dtype=np.float32).copy(), np.asarray(velocity, dtype=np.float32).copy())
        previous = self.frames.pop(int(frame), None)
        if previous is not None:
            self.bytes -= sum(a.nbytes for a in previous)
        self.frames[int(frame)] = value
        self.bytes += sum(a.nbytes for a in value)
        while self.bytes > self.budget and len(self.frames) > 1:
            _, removed = self.frames.popitem(last=False)
            self.bytes -= sum(a.nbytes for a in removed)

    def get(self, frame):
        return self.frames.get(int(frame))

    def clear(self):
        self.frames.clear()
        self.bytes = 0


def write_bake(path, metadata, frames, positions):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, schema=np.array("qubit.cloth_cache.v1"),
                            metadata=np.array(json.dumps(metadata, sort_keys=True)),
                            frames=np.asarray(frames, dtype=np.int32),
                            positions=np.asarray(positions, dtype=np.float32))
    temporary.replace(path)


def read_bake(path, expected_signature):
    with np.load(path, allow_pickle=False) as archive:
        if str(archive["schema"]) != "qubit.cloth_cache.v1":
            raise ValueError("Unsupported cloth cache.")
        metadata = json.loads(str(archive["metadata"]))
        if metadata.get("signature") != expected_signature:
            raise ValueError("Cloth cache is stale; bake again after changing the pattern or settings.")
        frames = archive["frames"].copy()
        positions = archive["positions"].copy()
    if positions.ndim != 3 or positions.shape[0] != len(frames) or positions.shape[-1] != 3 or not np.isfinite(positions).all():
        raise ValueError("Invalid cloth cache positions.")
    return metadata, {int(f): p for f, p in zip(frames, positions)}
