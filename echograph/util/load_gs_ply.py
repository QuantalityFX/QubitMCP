# echograph/util/load_gs_ply.py
from __future__ import annotations
from pathlib import Path
import numpy as np

C0 = 0.28209479177387814  # SH constant for l=0


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def read_header(p: Path):
    # Only collect properties that belong to the "vertex" element.
    props: list[str] = []
    count = None
    in_vertex = False

    with p.open("rb") as f:
        while True:
            line = f.readline()
            if not line:
                raise RuntimeError("Unexpected EOF in header")
            s = line.decode("utf-8", errors="replace").strip()

            if s.startswith("element "):
                parts = s.split()
                in_vertex = (len(parts) >= 3 and parts[1] == "vertex")
                if in_vertex:
                    count = int(parts[2])

            elif in_vertex and s.startswith("property"):
                parts = s.split()
                # property <type> <name>
                props.append(parts[-1])

            elif s == "end_header":
                header_end = f.tell()
                break

    return count, props, header_end


def load_gs_ply(ply_path: str, n: int = 200_000) -> np.ndarray:
    p = Path(ply_path)
    vcount, props, header_end = read_header(p)

    if vcount is None:
        raise RuntimeError("No vertex count found in header")

    # This file is binary_little_endian float32 for all listed properties
    floats_per_vertex = len(props)

    required = {
        "x","y","z",
        "f_dc_0","f_dc_1","f_dc_2",
        "opacity",
        "scale_0","scale_1","scale_2",
        "rot_0","rot_1","rot_2","rot_3",
    }
    missing = [k for k in sorted(required) if k not in set(props)]
    if missing:
        raise RuntimeError(f"PLY schema not supported by this loader. Missing: {missing}")
    # If there are extra float props, we just ignore them.

    n = min(n, vcount)

    with p.open("rb") as f:
        f.seek(header_end)
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

    # axis sizes in linear units (scales are typically log-space)
    axis_x = np.exp(s0).astype(np.float32)
    axis_y = np.exp(s1).astype(np.float32)
    axis_z = np.exp(s2).astype(np.float32)

    # base size (viewer knob)
    radius = np.exp((s0 + s1 + s2) / 3.0).astype(np.float32)

    # normalize so shader's (in_scale * in_rad) equals axis size
    eps = np.float32(1e-8)
    denom = np.maximum(radius, eps)

    sx = axis_x / denom
    sy = axis_y / denom
    sz = axis_z / denom

    # quaternion: many GS PLYs store (w,x,y,z) -> convert to (x,y,z,w)
    q = raw[:, [idx["rot_1"], idx["rot_2"], idx["rot_3"], idx["rot_0"]]].astype(np.float32)

    # normalize quaternion
    qn = np.linalg.norm(q, axis=1, keepdims=True).astype(np.float32)
    q = q / np.maximum(qn, eps)

    # Nx15: [x,y,z, r,g,b, a, radius, sx, sy, sz, qx, qy, qz, qw]
    splats = np.concatenate(
        [pos, rgb, a[:, None], radius[:, None], sx[:, None], sy[:, None], sz[:, None], q],
        axis=1,
    ).astype(np.float32)

    return splats


if __name__ == "__main__":
    ply = r"E:\GaussingSplats\models\bonsai\point_cloud\iteration_30000\point_cloud.ply"
    splats = load_gs_ply(ply, n=200_000)
    print("splats shape:", splats.shape)
    print("pos min/max:", splats[:, :3].min(axis=0), splats[:, :3].max(axis=0))
    print("rgb min/max:", splats[:, 3:6].min(), splats[:, 3:6].max())
    print("a min/max:", splats[:, 6].min(), splats[:, 6].max())
    print("r min/max:", splats[:, 7].min(), splats[:, 7].max())
    print("sx min/max:", splats[:, 8].min(), splats[:, 8].max())
    print("sy min/max:", splats[:, 9].min(), splats[:, 9].max())
    print("sz min/max:", splats[:, 10].min(), splats[:, 10].max())
    print("q first:", splats[0, 11:15])
