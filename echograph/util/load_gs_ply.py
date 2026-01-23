# echograph/util/load_gs_ply.py
from __future__ import annotations
from pathlib import Path
import numpy as np

C0 = 0.28209479177387814  # SH constant for l=0


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _read_header_full(p: Path):
    """
    Parses enough of a PLY header to support:
    - Uncompressed 3DGS PLY (float vertex props: x,y,z,...)
    - PlayCanvas/SuperSplat "compressed PLY" (element chunk + packed_* uint32)
    Returns: dict with element order, counts, props, and header_end offset.
    """
    elements = []
    current = None
    fmt = None

    with p.open("rb") as f:
        while True:
            line = f.readline()
            if not line:
                raise RuntimeError("Unexpected EOF in header")
            s = line.decode("utf-8", errors="replace").strip()

            if s.startswith("format "):
                parts = s.split()
                fmt = parts[1] if len(parts) > 1 else None

            elif s.startswith("element "):
                parts = s.split()
                if len(parts) >= 3:
                    if current is not None:
                        elements.append(current)
                    current = {"name": parts[1], "count": int(parts[2]), "props": []}

            elif s.startswith("property ") and current is not None:
                parts = s.split()
                # property <type> <name>
                if len(parts) >= 3:
                    current["props"].append((parts[1], parts[2]))

            elif s == "end_header":
                if current is not None:
                    elements.append(current)
                header_end = f.tell()
                break

    return {
        "format": fmt,
        "elements": elements,
        "header_end": header_end,
    }


def _find_element(header, name: str):
    name = (name or "").strip().lower()
    for el in header["elements"]:
        if (el.get("name") or "").strip().lower() == name:
            return el
    return None


def _load_uncompressed_float_schema(p: Path, header, n: int) -> np.ndarray:
    # Collect vertex float properties (names only, in order)
    v_el = _find_element(header, "vertex")
    if not v_el:
        raise RuntimeError("No vertex element found in PLY")

    vcount = int(v_el["count"])
    props = [nm for _ty, nm in (v_el.get("props") or [])]

    if vcount <= 0:
        raise RuntimeError("Invalid vertex count in PLY")

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

    floats_per_vertex = len(props)
    n = min(int(n), vcount)

    with p.open("rb") as f:
        f.seek(header["header_end"])
        raw = np.fromfile(f, dtype="<f4", count=n * floats_per_vertex)

    raw = raw.reshape(n, floats_per_vertex)
    idx = {name: i for i, name in enumerate(props)}

    pos = raw[:, [idx["x"], idx["y"], idx["z"]]]
    fdc = raw[:, [idx["f_dc_0"], idx["f_dc_1"], idx["f_dc_2"]]]
    rgb = np.clip(0.5 + C0 * fdc, 0.0, 1.0)

    a = sigmoid(raw[:, idx["opacity"]])

    s0 = raw[:, idx["scale_0"]]
    s1 = raw[:, idx["scale_1"]]
    s2 = raw[:, idx["scale_2"]]

    axis_x = np.exp(s0).astype(np.float32)
    axis_y = np.exp(s1).astype(np.float32)
    axis_z = np.exp(s2).astype(np.float32)

    radius = np.exp((s0 + s1 + s2) / 3.0).astype(np.float32)
    eps = np.float32(1e-8)
    denom = np.maximum(radius, eps)

    sx = axis_x / denom
    sy = axis_y / denom
    sz = axis_z / denom

    # stored as (w,x,y,z) in many 3DGS PLYs -> convert to (x,y,z,w)
    q = raw[:, [idx["rot_1"], idx["rot_2"], idx["rot_3"], idx["rot_0"]]].astype(np.float32)
    qn = np.linalg.norm(q, axis=1, keepdims=True).astype(np.float32)
    q = q / np.maximum(qn, eps)

    splats = np.concatenate(
        [pos, rgb, a[:, None], radius[:, None], sx[:, None], sy[:, None], sz[:, None], q],
        axis=1,
    ).astype(np.float32)

    return splats


def _load_supersplat_compressed(p: Path, header, n: int) -> np.ndarray:
    """
    Supports PlayCanvas/SuperSplat "compressed PLY":
    - element chunk: float mins/maxs for position, scale, color (per 256 splats)
    - element vertex: uint packed_position/rotation/scale/color
    - element sh: uchar f_rest_* (ignored here)
    Spec is described by PlayCanvas, and unpack logic mirrors gsply.reader.
    """
    if header.get("format") != "binary_little_endian":
        raise RuntimeError("Compressed PLY loader expects binary_little_endian")

    chunk_el = _find_element(header, "chunk")
    v_el = _find_element(header, "vertex")
    if not chunk_el or not v_el:
        raise RuntimeError("Compressed PLY missing chunk/vertex elements")

    chunk_count = int(chunk_el["count"])
    vcount = int(v_el["count"])
    n = min(int(n), vcount)

    # Validate vertex packed props exist
    vprops = [nm for _ty, nm in (v_el.get("props") or [])]
    need = {"packed_position", "packed_rotation", "packed_scale", "packed_color"}
    if not need.issubset(set(vprops)):
        raise RuntimeError(f"Compressed PLY missing packed fields: {sorted(need - set(vprops))}")

    # Chunk props order expected from SuperSplat header
    cprops = [nm for _ty, nm in (chunk_el.get("props") or [])]
    expected_chunk = [
        "min_x","min_y","min_z","max_x","max_y","max_z",
        "min_scale_x","min_scale_y","min_scale_z","max_scale_x","max_scale_y","max_scale_z",
        "min_r","min_g","min_b","max_r","max_g","max_b",
    ]
    if cprops[: len(expected_chunk)] != expected_chunk:
        # not fatal, but we need these names
        missing = [nm for nm in expected_chunk if nm not in set(cprops)]
        if missing:
            raise RuntimeError(f"Compressed PLY chunk schema unexpected/missing: {missing}")

    # Bit packing constants (PlayCanvas)
    INV_2047 = np.float32(1.0 / 2047.0)
    INV_1023 = np.float32(1.0 / 1023.0)
    INV_255  = np.float32(1.0 / 255.0)

    MASK_11 = np.uint32(0x7FF)
    MASK_10 = np.uint32(0x3FF)
    MASK_8  = np.uint32(0xFF)
    MASK_2  = np.uint32(0x3)

    POS_X_SHIFT = np.uint32(21)
    POS_Y_SHIFT = np.uint32(11)
    POS_Z_SHIFT = np.uint32(0)

    QUAT_INDEX_SHIFT = np.uint32(30)
    QUAT_A_SHIFT = np.uint32(20)
    QUAT_B_SHIFT = np.uint32(10)
    QUAT_C_SHIFT = np.uint32(0)

    COL_R_SHIFT = np.uint32(24)
    COL_G_SHIFT = np.uint32(16)
    COL_B_SHIFT = np.uint32(8)
    COL_O_SHIFT = np.uint32(0)

    QUAT_NORM = np.float32(1.4142135623730951)  # sqrt(2)
    CHUNK_SHIFT = 8  # 256 splats per chunk

    # Read binary blocks in element order: chunk, vertex, sh
    with p.open("rb") as f:
        f.seek(header["header_end"])

        # chunk data: float32
        chunk_floats_per = len(chunk_el.get("props") or [])
        chunk_raw = np.fromfile(f, dtype="<f4", count=chunk_count * chunk_floats_per)
        if chunk_raw.size != chunk_count * chunk_floats_per:
            raise RuntimeError("Failed reading chunk data block")
        chunk_raw = chunk_raw.reshape(chunk_count, chunk_floats_per)

        cidx = {nm: i for i, nm in enumerate(cprops)}
        min_x = chunk_raw[:, cidx["min_x"]]; max_x = chunk_raw[:, cidx["max_x"]]
        min_y = chunk_raw[:, cidx["min_y"]]; max_y = chunk_raw[:, cidx["max_y"]]
        min_z = chunk_raw[:, cidx["min_z"]]; max_z = chunk_raw[:, cidx["max_z"]]

        min_sx = chunk_raw[:, cidx["min_scale_x"]]; max_sx = chunk_raw[:, cidx["max_scale_x"]]
        min_sy = chunk_raw[:, cidx["min_scale_y"]]; max_sy = chunk_raw[:, cidx["max_scale_y"]]
        min_sz = chunk_raw[:, cidx["min_scale_z"]]; max_sz = chunk_raw[:, cidx["max_scale_z"]]

        min_r = chunk_raw[:, cidx["min_r"]]; max_r = chunk_raw[:, cidx["max_r"]]
        min_g = chunk_raw[:, cidx["min_g"]]; max_g = chunk_raw[:, cidx["max_g"]]
        min_b = chunk_raw[:, cidx["min_b"]]; max_b = chunk_raw[:, cidx["max_b"]]

        range_x = (max_x - min_x).astype(np.float32)
        range_y = (max_y - min_y).astype(np.float32)
        range_z = (max_z - min_z).astype(np.float32)

        range_sx = (max_sx - min_sx).astype(np.float32)
        range_sy = (max_sy - min_sy).astype(np.float32)
        range_sz = (max_sz - min_sz).astype(np.float32)

        range_r = (max_r - min_r).astype(np.float32)
        range_g = (max_g - min_g).astype(np.float32)
        range_b = (max_b - min_b).astype(np.float32)

        # vertex packed: 4x uint32 per vertex, in prop order
        # If order differs, build indices
        order = {nm: i for i, nm in enumerate(vprops)}
        packed_raw = np.fromfile(f, dtype="<u4", count=n * len(vprops))
        if packed_raw.size != n * len(vprops):
            raise RuntimeError("Failed reading packed vertex data block")
        packed_raw = packed_raw.reshape(n, len(vprops))

        packed_position = packed_raw[:, order["packed_position"]]
        packed_rotation = packed_raw[:, order["packed_rotation"]]
        packed_scale    = packed_raw[:, order["packed_scale"]]
        packed_color    = packed_raw[:, order["packed_color"]]

        # We can ignore the remaining vertices + sh block for viewport preview usage.

    # Decompress in vectorized NumPy
    idxs = np.arange(n, dtype=np.uint32)
    chunk_idx = (idxs >> np.uint32(CHUNK_SHIFT)).astype(np.int64)
    chunk_idx = np.clip(chunk_idx, 0, chunk_count - 1)

    # positions
    px = ((packed_position >> POS_X_SHIFT) & MASK_11).astype(np.float32) * INV_2047
    py = ((packed_position >> POS_Y_SHIFT) & MASK_10).astype(np.float32) * INV_1023
    pz = ((packed_position >> POS_Z_SHIFT) & MASK_11).astype(np.float32) * INV_2047

    x = min_x[chunk_idx] + px * range_x[chunk_idx]
    y = min_y[chunk_idx] + py * range_y[chunk_idx]
    z = min_z[chunk_idx] + pz * range_z[chunk_idx]
    pos = np.stack([x, y, z], axis=1).astype(np.float32)

    # scales (assumed to be log-scales like scale_0/1/2)
    sxq = ((packed_scale >> POS_X_SHIFT) & MASK_11).astype(np.float32) * INV_2047
    syq = ((packed_scale >> POS_Y_SHIFT) & MASK_10).astype(np.float32) * INV_1023
    szq = ((packed_scale >> POS_Z_SHIFT) & MASK_11).astype(np.float32) * INV_2047

    s0 = (min_sx[chunk_idx] + sxq * range_sx[chunk_idx]).astype(np.float32)
    s1 = (min_sy[chunk_idx] + syq * range_sy[chunk_idx]).astype(np.float32)
    s2 = (min_sz[chunk_idx] + szq * range_sz[chunk_idx]).astype(np.float32)

    axis_x = np.exp(s0).astype(np.float32)
    axis_y = np.exp(s1).astype(np.float32)
    axis_z = np.exp(s2).astype(np.float32)

    radius = np.exp((s0 + s1 + s2) / 3.0).astype(np.float32)
    eps = np.float32(1e-8)
    denom = np.maximum(radius, eps)

    sxn = axis_x / denom
    syn = axis_y / denom
    szn = axis_z / denom

    # colors
    cr = ((packed_color >> COL_R_SHIFT) & MASK_8).astype(np.float32) * INV_255
    cg = ((packed_color >> COL_G_SHIFT) & MASK_8).astype(np.float32) * INV_255
    cb = ((packed_color >> COL_B_SHIFT) & MASK_8).astype(np.float32) * INV_255
    co = ((packed_color >> COL_O_SHIFT) & MASK_8).astype(np.float32) * INV_255

    r = (min_r[chunk_idx] + cr * range_r[chunk_idx]).astype(np.float32)
    g = (min_g[chunk_idx] + cg * range_g[chunk_idx]).astype(np.float32)
    b = (min_b[chunk_idx] + cb * range_b[chunk_idx]).astype(np.float32)
    rgb = np.clip(np.stack([r, g, b], axis=1), 0.0, 1.0).astype(np.float32)

    a = np.clip(co, 0.0, 1.0).astype(np.float32)

    # quaternions (SuperSplat: (which:2)(a:10)(b:10)(c:10))
    qa = (((packed_rotation >> QUAT_A_SHIFT) & MASK_10).astype(np.float32) * INV_1023 - 0.5) * QUAT_NORM
    qb = (((packed_rotation >> QUAT_B_SHIFT) & MASK_10).astype(np.float32) * INV_1023 - 0.5) * QUAT_NORM
    qc = (((packed_rotation >> QUAT_C_SHIFT) & MASK_10).astype(np.float32) * INV_1023 - 0.5) * QUAT_NORM
    which = ((packed_rotation >> QUAT_INDEX_SHIFT) & MASK_2).astype(np.int32)

    m2 = 1.0 - (qa * qa + qb * qb + qc * qc)
    m = np.sqrt(np.maximum(m2, 0.0)).astype(np.float32)

    # quats are (w,x,y,z) in gsply; we build that then convert to (x,y,z,w)
    qw = np.empty(n, dtype=np.float32)
    qx = np.empty(n, dtype=np.float32)
    qy = np.empty(n, dtype=np.float32)
    qz = np.empty(n, dtype=np.float32)

    # which selects largest component position
    mask0 = which == 0
    mask1 = which == 1
    mask2 = which == 2
    mask3 = ~(mask0 | mask1 | mask2)

    qw[mask0], qx[mask0], qy[mask0], qz[mask0] = m[mask0], qa[mask0], qb[mask0], qc[mask0]
    qw[mask1], qx[mask1], qy[mask1], qz[mask1] = qa[mask1], m[mask1], qb[mask1], qc[mask1]
    qw[mask2], qx[mask2], qy[mask2], qz[mask2] = qa[mask2], qb[mask2], m[mask2], qc[mask2]
    qw[mask3], qx[mask3], qy[mask3], qz[mask3] = qa[mask3], qb[mask3], qc[mask3], m[mask3]

    # Convert to (x,y,z,w) to match the rest of your pipeline
    q = np.stack([qx, qy, qz, qw], axis=1).astype(np.float32)

    # Normalize quaternion
    qn = np.linalg.norm(q, axis=1, keepdims=True).astype(np.float32)
    q = q / np.maximum(qn, eps)

    splats = np.concatenate(
        [pos, rgb, a[:, None], radius[:, None], sxn[:, None], syn[:, None], szn[:, None], q],
        axis=1,
    ).astype(np.float32)

    return splats


def load_gs_ply(ply_path: str, n: int = 200_000) -> np.ndarray:
    p = Path(ply_path)
    header = _read_header_full(p)

    v_el = _find_element(header, "vertex")
    if not v_el:
        raise RuntimeError("No vertex element found in PLY")

    # Detect SuperSplat compressed PLY
    vprops = [nm for _ty, nm in (v_el.get("props") or [])]
    is_supersplat = _find_element(header, "chunk") is not None and "packed_position" in set(vprops)

    if is_supersplat:
        return _load_supersplat_compressed(p, header, n)

    # Default: uncompressed float schema
    return _load_uncompressed_float_schema(p, header, n)


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
