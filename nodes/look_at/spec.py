from __future__ import annotations

import math
from typing import Dict, Iterable, Tuple

from nodes.core import Spec


AXIS_OPTIONS = ("x", "-x", "y", "-y", "z", "-z")


def _ensure_param(node_item, name: str, default: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if not isinstance(params, list):
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(node_item, names: Iterable[str]) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for p in params:
        if isinstance(p, dict) and (p.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = p
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    hidden = {part.strip().lower() for part in str(entry.get("value") or "").split(",") if part.strip()}
    for name in names:
        hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        model.params = params
    except Exception:
        pass


def build_ports(node_item) -> None:
    _ensure_param(node_item, "target_owner", "")
    _ensure_param(node_item, "aim_axis", "-z")
    _ensure_param(node_item, "up_axis", "y")
    _ensure_param(node_item, "rotation_mode", "look_at")
    _ensure_param(node_item, "match_rotation", "1")
    _ensure_param(node_item, "match_position", "0")
    _ensure_param(node_item, "maintain_offset", "1")
    _ensure_param(node_item, "position_offset", "")
    _ensure_param(node_item, "position_axes", "xyz")
    _ensure_param(node_item, "live_update", "0")
    _ensure_hidden_params(node_item, ["target_owner", "position_offset", "live_update"])
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("object")
    if hasattr(node_item, "ensure_output"):
        node_item.ensure_output("object")


def _vec3(value, default=(0.0, 0.0, 0.0)) -> Tuple[float, float, float]:
    try:
        vals = list(value or [])
        if len(vals) >= 3:
            return (float(vals[0]), float(vals[1]), float(vals[2]))
    except Exception:
        pass
    return tuple(float(v) for v in default)


def _param_value(model, name: str) -> str:
    key = str(name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value") or "")
    return ""


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on", "y"}


def _axis(axis_name: str, default: str = "-z") -> Tuple[int, float, str]:
    key = str(axis_name or "").strip().lower().replace("+", "")
    if key not in AXIS_OPTIONS:
        key = default
    sign = -1.0 if key.startswith("-") else 1.0
    label = key[-1]
    idx = {"x": 0, "y": 1, "z": 2}[label]
    return idx, sign, key


def _normalize(v):
    x, y, z = (float(v[0]), float(v[1]), float(v[2]))
    n = math.sqrt(x * x + y * y + z * z)
    if n <= 1.0e-8:
        return None
    return (x / n, y / n, z / n)


def _dot(a, b) -> float:
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


def _cross(a, b):
    return (
        float(a[1] * b[2] - a[2] * b[1]),
        float(a[2] * b[0] - a[0] * b[2]),
        float(a[0] * b[1] - a[1] * b[0]),
    )


def _sub(a, b):
    return (float(a[0] - b[0]), float(a[1] - b[1]), float(a[2] - b[2]))


def _mul(v, s: float):
    return (float(v[0] * s), float(v[1] * s), float(v[2] * s))


def _fallback_up(aim_row):
    candidates = ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    best = candidates[0]
    best_abs = 2.0
    for cand in candidates:
        d = abs(_dot(cand, aim_row))
        if d < best_abs:
            best_abs = d
            best = cand
    return best


def _matrix_to_quat_rows(rows):
    m00, m01, m02 = rows[0]
    m10, m11, m12 = rows[1]
    m20, m21, m22 = rows[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(max(0.0, 1.0 + m00 - m11 - m22)) * 2.0
        return (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = math.sqrt(max(0.0, 1.0 + m11 - m00 - m22)) * 2.0
        return ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    s = math.sqrt(max(0.0, 1.0 + m22 - m00 - m11)) * 2.0
    return ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)


def _quat_to_matrix_rows(q):
    x, y, z, w = (float(q[0]), float(q[1]), float(q[2]), float(q[3]))
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n <= 1.0e-8:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    x, y, z, w = x / n, y / n, z / n, w / n
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)),
        (2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)),
        (2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)),
    )


def _rows_to_scene_euler(rows):
    # Scene matrices use R = Rx(-rx) @ Ry(-ry) @ Rz(-rz), row-vector style.
    r02 = max(-1.0, min(1.0, float(rows[0][2])))
    b = math.asin(-r02)
    cb = math.cos(b)
    if abs(cb) > 1.0e-8:
        a = math.atan2(float(rows[1][2]), float(rows[2][2]))
        c = math.atan2(float(rows[0][1]), float(rows[0][0]))
    else:
        a = 0.0
        c = math.atan2(-float(rows[1][0]), float(rows[1][1]))
    return (-math.degrees(a), -math.degrees(b), -math.degrees(c))


def look_at_rotation(source_pos, target_pos, *, aim_axis: str = "-z", up_axis: str = "y"):
    forward = _normalize(_sub(target_pos, source_pos))
    if forward is None:
        return None
    aim_idx, aim_sign, _ = _axis(aim_axis, "-z")
    up_idx, up_sign, _ = _axis(up_axis, "y")
    if up_idx == aim_idx:
        up_idx, up_sign = ((aim_idx + 1) % 3), 1.0

    rows = [None, None, None]
    rows[aim_idx] = _mul(forward, aim_sign)
    up_world = _fallback_up(rows[aim_idx])
    preferred = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}.get(
        str(up_axis or "y").strip().lower().replace("-", "").replace("+", ""),
        (0.0, 1.0, 0.0),
    )
    projected = _sub(preferred, _mul(rows[aim_idx], _dot(preferred, rows[aim_idx])))
    norm_up = _normalize(projected)
    if norm_up is None:
        projected = _sub(up_world, _mul(rows[aim_idx], _dot(up_world, rows[aim_idx])))
        norm_up = _normalize(projected)
    rows[up_idx] = _mul(norm_up or up_world, up_sign)

    missing = [idx for idx, row in enumerate(rows) if row is None]
    if missing:
        idx = missing[0]
        if idx == 0:
            rows[0] = _normalize(_cross(rows[1], rows[2]))
        elif idx == 1:
            rows[1] = _normalize(_cross(rows[2], rows[0]))
        else:
            rows[2] = _normalize(_cross(rows[0], rows[1]))
    if rows[0] is None or rows[1] is None or rows[2] is None:
        return None
    # Round-trip through a quaternion so the orientation stays orthonormal and
    # avoids incremental Euler/gimbal accumulation.
    return _rows_to_scene_euler(_quat_to_matrix_rows(_matrix_to_quat_rows(rows)))


def constraint_config(model) -> Dict[str, object]:
    return {
        "target_owner": _param_value(model, "target_owner").strip(),
        "aim_axis": (_param_value(model, "aim_axis") or "-z").strip().lower(),
        "up_axis": (_param_value(model, "up_axis") or "y").strip().lower(),
        "rotation_mode": (_param_value(model, "rotation_mode") or "look_at").strip().lower(),
        "match_rotation": _param_bool(model, "match_rotation", True),
        "match_position": _param_bool(model, "match_position", False),
        "maintain_offset": _param_bool(model, "maintain_offset", True),
        "position_offset": _param_value(model, "position_offset").strip(),
        "position_axes": (_param_value(model, "position_axes") or "xyz").strip().lower(),
        "live_update": _param_bool(model, "live_update", False),
    }


def apply_constraint(source_xform: dict, target_xform: dict, config: dict) -> dict:
    out = dict(source_xform or {})
    src_pos = _vec3(out.get("pos"), (0.0, 0.0, 0.0))
    tgt_pos = _vec3((target_xform or {}).get("pos"), (0.0, 0.0, 0.0))
    if bool(config.get("match_position", False)):
        pos = list(src_pos)
        axes = str(config.get("position_axes") or "xyz").lower()
        target_for_position = tgt_pos
        if bool(config.get("maintain_offset", True)):
            offset_text = str(config.get("position_offset") or "").strip()
            offset = None
            if offset_text:
                try:
                    parts = [float(part.strip()) for part in offset_text.split(",")]
                    if len(parts) >= 3:
                        offset = (parts[0], parts[1], parts[2])
                except Exception:
                    offset = None
            if offset is None:
                offset = _sub(src_pos, tgt_pos)
            target_for_position = (
                tgt_pos[0] + offset[0],
                tgt_pos[1] + offset[1],
                tgt_pos[2] + offset[2],
            )
        for axis, idx in (("x", 0), ("y", 1), ("z", 2)):
            if axis in axes:
                pos[idx] = target_for_position[idx]
        out["pos"] = pos
        src_pos = tuple(pos)
    if bool(config.get("match_rotation", True)):
        mode = str(config.get("rotation_mode") or "look_at").strip().lower()
        if mode in {"copy", "copy_target", "target", "exact", "match"}:
            out["rot"] = list(_vec3((target_xform or {}).get("rot"), (0.0, 0.0, 0.0)))
        else:
            rot = look_at_rotation(
                src_pos,
                tgt_pos,
                aim_axis=str(config.get("aim_axis") or "-z"),
                up_axis=str(config.get("up_axis") or "y"),
            )
            if rot is not None:
                out["rot"] = [float(rot[0]), float(rot[1]), float(rot[2])]
    out.setdefault("scl", list(_vec3((source_xform or {}).get("scl"), (1.0, 1.0, 1.0))))
    return out


LOOK_AT_SPEC = Spec(
    stripe_color="#38bdf8",
    build_ports=build_ports,
)
