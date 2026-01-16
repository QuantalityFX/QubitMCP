# echograph/util/load_gs_ply_sample.py
from __future__ import annotations
from pathlib import Path
import numpy as np

C0 = 0.28209479177387814  # SH constant for l=0

def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))

def read_header(p: Path):
    props = []
    count = None
    with p.open("rb") as f:
        while True:
            line = f.readline()
            if not line:
                raise RuntimeError("Unexpected EOF in header")
            s = line.decode("utf-8", errors="replace").strip()
            if s.startswith("element vertex"):
                count = int(s.split()[-1])
            elif s.startswith("property"):
                parts = s.split()
                # property float name
                props.append(parts[-1])
            elif s == "end_header":
                header_end = f.tell()
                break
    return count, props, header_end

def load_gs_ply_sample(ply_path: str, n: int = 200_000) -> np.ndarray:
    p = Path(ply_path)
    vcount, props, header_end = read_header(p)

    if vcount is None:
        raise RuntimeError("No vertex count found in header")

    # This file is binary_little_endian float32 for all listed properties
    floats_per_vertex = len(props)
    if floats_per_vertex != 62:
        raise RuntimeError(f"Expected 62 float props, got {floats_per_vertex}")

    n = min(n, vcount)

    with p.open("rb") as f:
        f.seek(header_end)
        # read only first n vertices
        raw = np.fromfile(f, dtype="<f4", count=n * floats_per_vertex)

    raw = raw.reshape(n, floats_per_vertex)

    # indices by name
    idx = {name: i for i, name in enumerate(props)}

    pos = raw[:, [idx["x"], idx["y"], idx["z"]]]

    fdc = raw[:, [idx["f_dc_0"], idx["f_dc_1"], idx["f_dc_2"]]]
    rgb = np.clip(0.5 + C0 * fdc, 0.0, 1.0)

    a = sigmoid(raw[:, idx["opacity"]])

    s0 = raw[:, idx["scale_0"]]
    s1 = raw[:, idx["scale_1"]]
    s2 = raw[:, idx["scale_2"]]
    radius = np.exp((s0 + s1 + s2) / 3.0)  # simple average radius for first pass

    splats = np.concatenate([pos, rgb, a[:, None], radius[:, None]], axis=1).astype(np.float32)
    return splats

if __name__ == "__main__":
    ply = r"E:\GaussingSplats\models\bicycle\point_cloud\iteration_30000\point_cloud.ply"
    splats = load_gs_ply_sample(ply, n=200_000)
    print("splats shape:", splats.shape)
    print("pos min/max:", splats[:, :3].min(axis=0), splats[:, :3].max(axis=0))
    print("rgb min/max:", splats[:, 3:6].min(), splats[:, 3:6].max())
    print("a min/max:", splats[:, 6].min(), splats[:, 6].max())
    print("r min/max:", splats[:, 7].min(), splats[:, 7].max())
