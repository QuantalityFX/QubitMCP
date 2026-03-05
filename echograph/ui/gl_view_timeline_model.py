from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
except Exception:
    np = None

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.ui.fps_camera import FpsCamera



class GraphGLTimelineModelMixin:
    def _timeline_current_frame(self) -> int:
        spin = getattr(self, "_timeline_frame_spin", None)
        if spin is None:
            return 0
        try:
            return int(spin.value())
        except Exception:
            return 0

    def _timeline_target_owner(self) -> str:
        try:
            return str(getattr(self, "_timeline_owner_name", "") or "").strip()
        except Exception:
            return ""

    def _timeline_owner_file_paths(self) -> List[Path]:
        scene_name = str(getattr(self, "_timeline_scene_name", "") or "").strip()
        if not scene_name:
            scene_name = self._timeline_default_scene_name()
        if not scene_name:
            scene_name = "scene"
        try:
            base_dir = self._timeline_default_project_dir()
        except Exception:
            return []
        out_dir = Path(base_dir) / "projects"
        if not out_dir.exists():
            return []
        try:
            safe_scene = self._timeline_safe_name(scene_name)
        except Exception:
            return []
        try:
            return sorted(out_dir.glob(f"{safe_scene}__owner_*_timeline.json"))
        except Exception:
            return []

    def _timeline_parse_keys_rows(self, rows) -> Dict[int, Dict[str, object]]:
        data: Dict[int, Dict[str, object]] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                frame = int(row.get("frame", 0))
            except Exception:
                continue
            if frame < 0:
                continue
            item: Dict[str, object] = {}
            xyz = row.get("xyz", None)
            if isinstance(xyz, (list, tuple)) and len(xyz) >= 3:
                try:
                    item["xyz"] = [float(xyz[0]), float(xyz[1]), float(xyz[2])]
                except Exception:
                    pass
            rxyz = row.get("rxyz", None)
            if isinstance(rxyz, (list, tuple)) and len(rxyz) >= 3:
                try:
                    item["rxyz"] = [float(rxyz[0]), float(rxyz[1]), float(rxyz[2])]
                except Exception:
                    pass
            st = row.get("camera_state", None)
            if isinstance(st, dict):
                item["camera_state"] = st
            mask = row.get("axis_mask", None)
            if isinstance(mask, (list, tuple)) and len(mask) >= 6:
                try:
                    item["axis_mask"] = [bool(mask[i]) for i in range(6)]
                except Exception:
                    pass
            ch = row.get("curve_handles", None)
            if isinstance(ch, dict) and ch:
                ch_out = {}
                for ak, av in ch.items():
                    try:
                        axis_idx = int(str(ak).strip())
                    except Exception:
                        continue
                    if axis_idx < 0 or axis_idx > 5:
                        continue
                    if not isinstance(av, dict):
                        continue
                    mode_raw = str(av.get("mode", "tied") or "tied").strip().lower()
                    if mode_raw == "straight":
                        mode = "straight"
                    elif mode_raw == "untied":
                        mode = "untied"
                    else:
                        mode = "tied"
                    if mode == "straight":
                        ch_out[str(axis_idx)] = {"mode": "straight"}
                        continue
                    in_raw = av.get("in", None)
                    out_raw = av.get("out", None)
                    if not (isinstance(in_raw, (list, tuple)) and len(in_raw) >= 2):
                        continue
                    if not (isinstance(out_raw, (list, tuple)) and len(out_raw) >= 2):
                        continue
                    try:
                        ch_out[str(axis_idx)] = {
                            "mode": mode,
                            "in": [float(in_raw[0]), float(in_raw[1])],
                            "out": [float(out_raw[0]), float(out_raw[1])],
                        }
                    except Exception:
                        continue
                if ch_out:
                    item["curve_handles"] = ch_out
            if item:
                data[int(frame)] = item
        return data

    def _timeline_read_owner_keys_file(self, path: Path) -> Tuple[str, Dict[int, Dict[str, object]]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ("", {})
        if not isinstance(raw, dict):
            return ("", {})
        owner = str(raw.get("owner", "") or "").strip()
        if not owner:
            return ("", {})
        rows = raw.get("keys", []) or []
        data = self._timeline_parse_keys_rows(rows)
        return (owner, data)

    def _timeline_collect_other_owner_keys(self) -> List[Tuple[str, Dict[int, Dict[str, object]]]]:
        cur_owner_norm = self._timeline_owner_norm(self._timeline_target_owner())
        file_paths = self._timeline_owner_file_paths()
        cache = getattr(self, "_timeline_owner_keys_cache", None)
        if not isinstance(cache, dict):
            cache = {}
        file_set = {str(p) for p in file_paths}
        for stale_key in [k for k in list(cache.keys()) if str(k) not in file_set]:
            try:
                del cache[stale_key]
            except Exception:
                pass
        out: List[Tuple[str, Dict[int, Dict[str, object]]]] = []
        seen_owner_norm = set()
        for path in file_paths:
            path_key = str(path)
            mtime = None
            try:
                mtime = float(path.stat().st_mtime)
            except Exception:
                mtime = None
            entry = cache.get(path_key) if isinstance(cache, dict) else None
            owner = ""
            keys_map: Dict[int, Dict[str, object]] = {}
            if isinstance(entry, dict) and entry.get("mtime", None) == mtime:
                owner = str(entry.get("owner", "") or "").strip()
                cached_keys = entry.get("keys", None)
                if isinstance(cached_keys, dict):
                    keys_map = cached_keys
            else:
                owner, keys_map = self._timeline_read_owner_keys_file(path)
                cache[path_key] = {
                    "mtime": mtime,
                    "owner": str(owner or ""),
                    "keys": keys_map if isinstance(keys_map, dict) else {},
                }
            owner_norm = self._timeline_owner_norm(owner)
            if not owner_norm or owner_norm == cur_owner_norm:
                continue
            if owner_norm in seen_owner_norm:
                continue
            seen_owner_norm.add(owner_norm)
            if isinstance(keys_map, dict) and keys_map:
                out.append((str(owner), keys_map))
        self._timeline_owner_keys_cache = cache
        return out

    def _timeline_owner_current_xyz(self, owner: str) -> Tuple[float, float, float]:
        try:
            xf, _is_splat = self._timeline_get_owner_xform(str(owner or "").strip())
            if isinstance(xf, dict):
                pos = xf.get("pos", None)
                if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                    return (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            pass
        return (0.0, 0.0, 0.0)

    def _timeline_eval_frame_values_for_owner_keys(
        self,
        owner: str,
        keys_map: Dict[int, Dict[str, object]],
        frame: int,
    ) -> Tuple[Optional[Tuple[float, float, float]], Optional[Tuple[float, float, float]]]:
        old_owner = getattr(self, "_timeline_owner_name", None)
        old_keys = getattr(self, "_timeline_keys", None)
        try:
            self._timeline_owner_name = str(owner or "").strip() or None
            self._timeline_keys = keys_map if isinstance(keys_map, dict) else {}
            return self._timeline_eval_frame_values(int(frame))
        except Exception:
            return (None, None)
        finally:
            self._timeline_owner_name = old_owner
            self._timeline_keys = old_keys if isinstance(old_keys, dict) else {}

    def _timeline_apply_other_owner_frames(self, frame: int) -> None:
        try:
            f = int(frame)
        except Exception:
            f = 0
        for owner, keys_map in self._timeline_collect_other_owner_keys():
            if not owner or not isinstance(keys_map, dict) or not keys_map:
                continue
            xyz_eval, rxyz_eval = self._timeline_eval_frame_values_for_owner_keys(owner, keys_map, f)
            if xyz_eval is None and rxyz_eval is None:
                continue
            if xyz_eval is None:
                xyz_eval = self._timeline_owner_current_xyz(owner)
            try:
                self._timeline_clear_manual_override(owner)
            except Exception:
                pass
            try:
                self._timeline_apply_owner_xyz_only(owner, xyz_eval, rxyz=rxyz_eval)
            except Exception:
                continue

    def _timeline_owner_norm(self, owner: str) -> str:
        try:
            return str(owner or "").strip().lower()
        except Exception:
            return ""

    def _timeline_mark_manual_override(self, owner: str) -> None:
        key = self._timeline_owner_norm(owner)
        if not key:
            return
        overrides = getattr(self, "_timeline_manual_override_owners", None)
        if not isinstance(overrides, set):
            overrides = set()
        overrides.add(key)
        self._timeline_manual_override_owners = overrides

    def _timeline_clear_manual_override(self, owner: Optional[str] = None) -> None:
        overrides = getattr(self, "_timeline_manual_override_owners", None)
        if not isinstance(overrides, set):
            self._timeline_manual_override_owners = set()
            return
        if owner is None:
            overrides.clear()
            self._timeline_manual_override_owners = overrides
            return
        key = self._timeline_owner_norm(owner)
        if not key:
            return
        try:
            overrides.discard(key)
        except Exception:
            pass
        self._timeline_manual_override_owners = overrides

    def _timeline_owner_is_splat(self, owner: str) -> bool:
        key = str(owner or "").strip()
        if not key:
            return False
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            splat_map = getattr(renderer, "_mgl_scene_splats", None)
            if not isinstance(splat_map, dict) or not splat_map:
                splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
            if not isinstance(splat_map, dict):
                return False
            if key in splat_map:
                return True
            lk = key.lower()
            for k in splat_map.keys():
                if str(k).strip().lower() == lk:
                    return True
        except Exception:
            pass
        return False

    def _timeline_get_owner_xform(self, owner: str):
        key = str(owner or "").strip()
        if not key:
            return None, False
        try:
            renderer = getattr(self, "_mgl_renderer", None) or self
            is_splat = self._timeline_owner_is_splat(key)
            getf = (
                getattr(renderer, "_mgl_get_scene_splat_xform", None)
                if is_splat
                else getattr(renderer, "_mgl_get_scene_asset_xform", None)
            )
            if not callable(getf):
                return None, is_splat
            xf = getf(key)
            if not isinstance(xf, dict):
                return None, is_splat
            return xf, is_splat
        except Exception:
            return None, False

    def _timeline_current_cam_xyz(self):
        owner = self._timeline_target_owner()
        if owner:
            xf, _is_splat = self._timeline_get_owner_xform(owner)
            if isinstance(xf, dict):
                pos = xf.get("pos", None)
                if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                    try:
                        return (float(pos[0]), float(pos[1]), float(pos[2]))
                    except Exception:
                        pass
            return (0.0, 0.0, 0.0)
        if np is None:
            return None
        try:
            cam = getattr(self, "_fps_camera", None)
            if cam is not None and bool(getattr(self, "_fps_camera_active", False)):
                pos = np.array(getattr(cam, "position", (0.0, 0.0, 0.0)), dtype=np.float32)
                return (float(pos[0]), float(pos[1]), float(pos[2]))
        except Exception:
            pass
        try:
            c = self._calc_camera_world_orbit()
            if c is not None:
                return (float(c[0]), float(c[1]), float(c[2]))
        except Exception:
            pass
        try:
            cw = getattr(self, "_mgl_cam_world", None)
            if isinstance(cw, (list, tuple)) and len(cw) >= 3:
                return (float(cw[0]), float(cw[1]), float(cw[2]))
        except Exception:
            pass
        return None

    def _timeline_current_cam_rxyz(self):
        owner = self._timeline_target_owner()
        if owner:
            xf, _is_splat = self._timeline_get_owner_xform(owner)
            if isinstance(xf, dict):
                rot = xf.get("rot", None)
                if isinstance(rot, (list, tuple)) and len(rot) >= 3:
                    try:
                        return (float(rot[0]), float(rot[1]), float(rot[2]))
                    except Exception:
                        pass
            return (0.0, 0.0, 0.0)
        try:
            cam = getattr(self, "_fps_camera", None)
            if cam is not None and bool(getattr(self, "_fps_camera_active", False)):
                fwd = getattr(cam, "forward", None)
                if fwd is not None and np is not None:
                    fv = np.array(fwd, dtype=np.float32).reshape(3)
                    fn = float(np.linalg.norm(fv))
                    if fn > 1e-6:
                        fv = fv / fn
                        pitch = math.degrees(math.asin(max(-1.0, min(1.0, float(fv[1])))))
                        yaw = math.degrees(math.atan2(float(fv[0]), float(-fv[2])))
                        return (float(pitch), float(yaw), 0.0)
        except Exception:
            pass
        try:
            pitch = math.degrees(float(getattr(self, "_mgl_orbit_pitch", 0.0)))
            yaw = math.degrees(float(getattr(self, "_mgl_orbit_yaw", 0.0)))
            return (float(pitch), float(yaw), 0.0)
        except Exception:
            pass
        return (0.0, 0.0, 0.0)

    def _timeline_format_coord(self, val) -> str:
        try:
            return f"{float(val):.3f}"
        except Exception:
            return "0.000"

    def _timeline_on_coord_input_committed(self, axis: int, widget=None) -> None:
        if bool(getattr(self, "_timeline_coord_syncing", False)):
            return
        try:
            idx = int(axis)
        except Exception:
            return
        if idx < 0 or idx > 5:
            return
        if widget is None:
            return
        try:
            value = float(widget.value())
        except Exception:
            try:
                value = float(str(widget.text()).strip())
            except Exception:
                return
        try:
            frame = max(0, int(self._timeline_current_frame()))
        except Exception:
            frame = 0
        keys = getattr(self, "_timeline_keys", {}) or {}
        entry = keys.get(int(frame))
        if not isinstance(entry, dict):
            entry = {}
        self._timeline_set_axis_value_for_entry(
            entry,
            int(idx),
            float(value),
            frame=int(frame),
        )
        entry["camera_state"] = {}
        keys[int(frame)] = entry
        self._timeline_keys = keys
        self._timeline_total_max = max(int(getattr(self, "_timeline_total_max", 240) or 240), int(frame))
        self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
        try:
            self._timeline_apply_xyz_only(entry.get("xyz", None), entry.get("rxyz", None))
            self.update()
        except Exception:
            pass
        self._timeline_save_to_disk()
        self._timeline_refresh_coord_labels()

    def _timeline_update_key_count_label(self) -> None:
        lbl = getattr(self, "_timeline_key_count_label", None)
        if lbl is None:
            return
        try:
            count = len(getattr(self, "_timeline_keys", {}) or {})
        except Exception:
            count = 0
        lbl.setText(f"Keys: {int(count)}")

    def _timeline_max_known_frame(self) -> int:
        max_key = 0
        try:
            keys = getattr(self, "_timeline_keys", {}) or {}
            if keys:
                max_key = max(int(k) for k in keys.keys())
        except Exception:
            max_key = 0
        marker_max = 0
        try:
            in_frame, out_frame = self._timeline_in_out_frames()
            vals = [int(v) for v in (in_frame, out_frame) if v is not None]
            if vals:
                marker_max = max(vals)
        except Exception:
            marker_max = 0
        try:
            cur = int(self._timeline_current_frame())
        except Exception:
            cur = 0
        try:
            base_total = int(getattr(self, "_timeline_total_max", 240) or 240)
        except Exception:
            base_total = 240
        return max(240, base_total, max_key, marker_max, cur)

    def _timeline_sync_range_controls(
        self,
        *,
        keep_current_visible: bool = True,
        refresh_key_markers: bool = False,
    ) -> None:
        slider = getattr(self, "_timeline_frame_slider", None)
        if slider is None:
            return
        try:
            prev_start = int(getattr(self, "_timeline_view_start", 0) or 0)
        except Exception:
            prev_start = 0
        try:
            prev_local_max = int(max(0, int(slider.maximum())))
        except Exception:
            prev_local_max = 0
        try:
            frame = max(0, int(self._timeline_current_frame()))
        except Exception:
            frame = 0
        total = int(max(0, self._timeline_max_known_frame()))
        self._timeline_total_max = total
        try:
            span = int(getattr(self, "_timeline_view_span", 120) or 120)
        except Exception:
            span = 120
        span = max(24, span)
        span = min(span, total) if total > 0 else span
        start_max = max(0, total - span)
        try:
            start = int(getattr(self, "_timeline_view_start", 0) or 0)
        except Exception:
            start = 0
        if keep_current_visible:
            if frame < start:
                start = frame
            elif frame > (start + span):
                start = frame - span
        start = max(0, min(start, start_max))
        self._timeline_view_start = start

        scroll = getattr(self, "_timeline_scrollbar", None)
        if scroll is not None:
            try:
                scroll.blockSignals(True)
                scroll.setRange(0, start_max)
                scroll.setSingleStep(max(1, span // 20))
                scroll.setPageStep(max(1, span))
                scroll.setValue(start)
            except Exception:
                pass
            finally:
                try:
                    scroll.blockSignals(False)
                except Exception:
                    pass

        local_max = max(0, min(span, total - start))
        local_val = max(0, min(local_max, frame - start))
        try:
            slider.blockSignals(True)
            slider.setRange(0, local_max)
            slider.setValue(local_val)
        except Exception:
            pass
        finally:
            try:
                slider.blockSignals(False)
            except Exception:
                pass

        self._timeline_update_tick_labels()
        if bool(refresh_key_markers) or int(start) != int(prev_start) or int(local_max) != int(prev_local_max):
            self._timeline_update_key_markers()
        self._timeline_update_playhead()

    def _timeline_target_label_text(self) -> str:
        owner = self._timeline_target_owner()
        if owner:
            return owner
        try:
            mode = str(getattr(self, "_camera_select_mode", "default") or "default").strip()
        except Exception:
            mode = "default"
        if mode and mode.lower() != "default":
            return mode
        return "Default Camera"

    def _timeline_update_target_label(self) -> None:
        lbl = getattr(self, "_timeline_target_label", None)
        if lbl is None:
            return
        text = str(self._timeline_target_label_text() or "").strip()
        try:
            lbl.setText(text)
            lbl.setToolTip(text)
            lbl.setVisible(bool(text))
        except Exception:
            pass

    def _timeline_update_tick_labels(self) -> None:
        slider = getattr(self, "_timeline_frame_slider", None)
        ticks_frame = getattr(self, "_timeline_ticks_frame", None)
        labels = getattr(self, "_timeline_tick_labels", None)
        if slider is None or ticks_frame is None:
            return
        if not isinstance(labels, list):
            labels = []
        try:
            local_max = int(max(0, int(slider.maximum())))
        except Exception:
            local_max = 0
        try:
            start = int(max(0, int(getattr(self, "_timeline_view_start", 0) or 0)))
        except Exception:
            start = 0
        end = int(start + local_max)
        first_major = int(((start + 14) // 15) * 15)
        majors = []
        for frame_val in range(first_major, end + 1, 15):
            try:
                local = int(frame_val - start)
                x = self._timeline_slider_value_to_x(local, slider=slider)
                if x is None:
                    continue
                majors.append((int(frame_val), int(x)))
            except Exception:
                continue

        while len(labels) < len(majors):
            try:
                lb = QtWidgets.QLabel("", ticks_frame)
                lb.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
                labels.append(lb)
            except Exception:
                pass

        for i, lb in enumerate(labels):
            if lb is None:
                continue
            if i >= len(majors):
                try:
                    lb.hide()
                except Exception:
                    pass
                continue
            frame_val, x = majors[i]
            text = str(int(frame_val))
            try:
                fm = lb.fontMetrics()
                width = max(16, int(fm.horizontalAdvance(text)) + 8)
            except Exception:
                width = max(16, (len(text) * 8) + 8)
            try:
                height = int(max(12, ticks_frame.height()))
            except Exception:
                height = 14
            try:
                lb.setText(text)
                lb.setGeometry(int(round(float(x) - (float(width) * 0.5))), 0, int(width), int(height))
                lb.show()
            except Exception:
                pass
        self._timeline_tick_labels = labels
        self._timeline_update_target_label()

    def _timeline_sync_row_alignment(self) -> None:
        spacer = getattr(self, "_timeline_left_header_spacer", None)
        tracks = getattr(self, "_timeline_tracks_frame", None)
        if spacer is None or tracks is None:
            return
        try:
            top = int(max(0, tracks.y()))
        except Exception:
            return
        try:
            if int(spacer.height()) != int(top):
                spacer.setFixedHeight(int(top))
        except Exception:
            pass

    def _timeline_clear_key_markers(self) -> None:
        markers = getattr(self, "_timeline_key_markers", None)
        if not isinstance(markers, list):
            self._timeline_key_markers = []
            return
        for row in markers:
            if not isinstance(row, list):
                continue
            for dot in row:
                if dot is None:
                    continue
                try:
                    dot.hide()
                    dot.deleteLater()
                except Exception:
                    pass
        self._timeline_key_markers = []

    def _timeline_entry_axis_mask(self, entry) -> List[bool]:
        if not isinstance(entry, dict):
            return [False, False, False, False, False, False]
        raw = entry.get("axis_mask", None)
        if isinstance(raw, (list, tuple)) and len(raw) >= 6:
            try:
                return [bool(raw[i]) for i in range(6)]
            except Exception:
                pass
        mask = [False, False, False, False, False, False]
        xyz = entry.get("xyz", None)
        if isinstance(xyz, (list, tuple)) and len(xyz) >= 3:
            mask[0] = True
            mask[1] = True
            mask[2] = True
        rxyz = entry.get("rxyz", None)
        if isinstance(rxyz, (list, tuple)) and len(rxyz) >= 3:
            mask[3] = True
            mask[4] = True
            mask[5] = True
        return mask

    def _timeline_entry_has_any_axis(self, entry) -> bool:
        try:
            return any(self._timeline_entry_axis_mask(entry))
        except Exception:
            return False

    def _timeline_axis_is_keyed(self, entry, axis: int) -> bool:
        try:
            idx = int(axis)
        except Exception:
            return False
        if idx < 0 or idx > 5:
            return False
        try:
            return bool(self._timeline_entry_axis_mask(entry)[idx])
        except Exception:
            return False

    def _timeline_set_axis_keyed(self, entry, axis: int, keyed: bool) -> None:
        if not isinstance(entry, dict):
            return
        try:
            idx = int(axis)
        except Exception:
            return
        if idx < 0 or idx > 5:
            return
        mask = self._timeline_entry_axis_mask(entry)
        mask[idx] = bool(keyed)
        entry["axis_mask"] = mask
        if not bool(keyed):
            try:
                self._timeline_remove_entry_axis_handles(entry, int(idx))
            except Exception:
                pass

    def _timeline_axis_value_for_entry(self, entry, axis: int) -> Optional[float]:
        if not isinstance(entry, dict):
            return None
        try:
            idx = int(axis)
        except Exception:
            return None
        if idx < 0 or idx > 5:
            return None
        try:
            if idx < 3:
                arr = entry.get("xyz", None)
                if isinstance(arr, (list, tuple)) and len(arr) >= 3:
                    return float(arr[idx])
            else:
                arr = entry.get("rxyz", None)
                if isinstance(arr, (list, tuple)) and len(arr) >= 3:
                    return float(arr[idx - 3])
        except Exception:
            return None
        return None

    def _timeline_set_axis_value_for_entry(
        self,
        entry,
        axis: int,
        value: float,
        *,
        frame: Optional[int] = None,
    ) -> None:
        if not isinstance(entry, dict):
            return
        try:
            idx = int(axis)
            val = float(value)
        except Exception:
            return
        if idx < 0 or idx > 5:
            return
        # Ensure edits on a single axis do not implicitly key other axes when
        # creating/patching sparse entries during curve dragging.
        raw_mask = entry.get("axis_mask", None)
        if not (isinstance(raw_mask, (list, tuple)) and len(raw_mask) >= 6):
            has_xyz = isinstance(entry.get("xyz", None), (list, tuple)) and len(entry.get("xyz", None)) >= 3
            has_rxyz = isinstance(entry.get("rxyz", None), (list, tuple)) and len(entry.get("rxyz", None)) >= 3
            if has_xyz or has_rxyz:
                entry["axis_mask"] = self._timeline_entry_axis_mask(entry)
            else:
                entry["axis_mask"] = [False, False, False, False, False, False]
        if idx < 3:
            arr = entry.get("xyz", None)
            if not (isinstance(arr, (list, tuple)) and len(arr) >= 3):
                xyz0 = self._timeline_current_cam_xyz()
                if xyz0 is None:
                    arr = [0.0, 0.0, 0.0]
                else:
                    arr = [float(xyz0[0]), float(xyz0[1]), float(xyz0[2])]
            else:
                try:
                    arr = [float(arr[0]), float(arr[1]), float(arr[2])]
                except Exception:
                    arr = [0.0, 0.0, 0.0]
            arr[idx] = val
            entry["xyz"] = arr
        else:
            ridx = idx - 3
            arr = entry.get("rxyz", None)
            if not (isinstance(arr, (list, tuple)) and len(arr) >= 3):
                rxyz0 = self._timeline_current_cam_rxyz()
                if rxyz0 is None:
                    arr = [0.0, 0.0, 0.0]
                else:
                    arr = [float(rxyz0[0]), float(rxyz0[1]), float(rxyz0[2])]
            else:
                try:
                    arr = [float(arr[0]), float(arr[1]), float(arr[2])]
                except Exception:
                    arr = [0.0, 0.0, 0.0]
            arr[ridx] = val
            entry["rxyz"] = arr
        self._timeline_set_axis_keyed(entry, idx, True)
        try:
            frm = int(frame) if frame is not None else None
        except Exception:
            frm = None
        if frm is not None and int(frm) >= 0:
            try:
                self._timeline_initialize_entry_axis_handle_if_missing(
                    entry,
                    int(idx),
                    int(frm),
                    key_value=float(val),
                )
            except Exception:
                pass
        entry["camera_state"] = {}

    def _timeline_axes_for_entry(self, entry) -> Tuple[int, ...]:
        if not isinstance(entry, dict):
            return ()
        mask = self._timeline_entry_axis_mask(entry)
        axes = [i for i in range(6) if bool(mask[i])]
        return tuple(axes)

    def _timeline_axis_key_points(self, axis: int) -> List[Tuple[int, float]]:
        try:
            idx = int(axis)
        except Exception:
            return []
        if idx < 0 or idx > 5:
            return []
        keys = getattr(self, "_timeline_keys", {}) or {}
        try:
            items = sorted(keys.items(), key=lambda kv: int(kv[0]))
        except Exception:
            items = list(keys.items())
        out: List[Tuple[int, float]] = []
        for frame_raw, entry in items:
            try:
                frame = int(frame_raw)
            except Exception:
                continue
            if not self._timeline_axis_is_keyed(entry, idx):
                continue
            val = self._timeline_axis_value_for_entry(entry, idx)
            if val is None:
                continue
            out.append((frame, float(val)))
        return out

    def _timeline_axis_neighbor_frames(self, axis: int, frame: int) -> Tuple[Optional[int], Optional[int]]:
        prev_pt, next_pt = self._timeline_axis_neighbor_points(axis, frame)
        prev_frame = None
        next_frame = None
        try:
            if isinstance(prev_pt, tuple) and len(prev_pt) >= 1:
                prev_frame = int(prev_pt[0])
        except Exception:
            prev_frame = None
        try:
            if isinstance(next_pt, tuple) and len(next_pt) >= 1:
                next_frame = int(next_pt[0])
        except Exception:
            next_frame = None
        return (prev_frame, next_frame)

    def _timeline_axis_neighbor_points(
        self,
        axis: int,
        frame: int,
    ) -> Tuple[Optional[Tuple[int, float]], Optional[Tuple[int, float]]]:
        try:
            idx = int(axis)
            cur = int(frame)
        except Exception:
            return (None, None)
        prev_pt = None
        next_pt = None
        for ff, vv in self._timeline_axis_key_points(idx):
            if int(ff) < int(cur):
                prev_pt = (int(ff), float(vv))
                continue
            if int(ff) > int(cur):
                next_pt = (int(ff), float(vv))
                break
        return (prev_pt, next_pt)

    def _timeline_default_axis_handles(
        self,
        axis: int,
        frame: int,
        *,
        key_value: Optional[float] = None,
    ) -> Dict[str, object]:
        try:
            idx = int(axis)
            cur = int(frame)
        except Exception:
            return {"mode": "tied", "in": (-3.0, 0.0), "out": (3.0, 0.0)}
        prev_pt, next_pt = self._timeline_axis_neighbor_points(idx, cur)
        prev_frame = int(prev_pt[0]) if isinstance(prev_pt, tuple) and len(prev_pt) >= 2 else None
        next_frame = int(next_pt[0]) if isinstance(next_pt, tuple) and len(next_pt) >= 2 else None
        prev_val = float(prev_pt[1]) if isinstance(prev_pt, tuple) and len(prev_pt) >= 2 else None
        next_val = float(next_pt[1]) if isinstance(next_pt, tuple) and len(next_pt) >= 2 else None

        cur_val = None
        if key_value is not None:
            try:
                cur_val = float(key_value)
            except Exception:
                cur_val = None
        if cur_val is None:
            try:
                entry = (getattr(self, "_timeline_keys", {}) or {}).get(int(cur))
                cur_val = self._timeline_axis_value_for_entry(entry, int(idx))
            except Exception:
                cur_val = None
        if cur_val is None:
            if prev_val is not None and next_val is not None:
                cur_val = (float(prev_val) + float(next_val)) * 0.5
            elif next_val is not None:
                cur_val = float(next_val)
            elif prev_val is not None:
                cur_val = float(prev_val)
            else:
                cur_val = 0.0

        tangent = 0.0
        try:
            if (
                prev_frame is not None
                and next_frame is not None
                and int(next_frame) != int(prev_frame)
                and prev_val is not None
                and next_val is not None
            ):
                tangent = (float(next_val) - float(prev_val)) / float(int(next_frame) - int(prev_frame))
            elif (
                next_frame is not None
                and int(next_frame) != int(cur)
                and next_val is not None
            ):
                tangent = (float(next_val) - float(cur_val)) / float(int(next_frame) - int(cur))
            elif (
                prev_frame is not None
                and int(cur) != int(prev_frame)
                and prev_val is not None
            ):
                tangent = (float(cur_val) - float(prev_val)) / float(int(cur) - int(prev_frame))
        except Exception:
            tangent = 0.0

        base = 3.0
        try:
            gaps = []
            if prev_frame is not None:
                gaps.append(max(1.0, float(cur - int(prev_frame))))
            if next_frame is not None:
                gaps.append(max(1.0, float(int(next_frame) - cur)))
            if gaps:
                base = min(gaps) * 0.33
        except Exception:
            base = 3.0
        base = max(0.6, min(4.0, float(base)))
        out_dy = float(tangent) * float(base)
        max_dy = 4.0
        try:
            vmin = float(getattr(self, "_timeline_curve_min", -5.0))
            vmax = float(getattr(self, "_timeline_curve_max", 5.0))
            if vmax < vmin:
                vmin, vmax = vmax, vmin
            vspan = max(1.0, float(vmax) - float(vmin))
            max_dy = max(1.0, min(6.0, float(vspan) * 0.45))
        except Exception:
            max_dy = 4.0
        out_dy = max(-float(max_dy), min(float(max_dy), float(out_dy)))
        return {"mode": "tied", "in": (-float(base), -float(out_dy)), "out": (float(base), float(out_dy))}

    def _timeline_clamp_axis_handle_pair(
        self,
        axis: int,
        frame: int,
        in_vec: Tuple[float, float],
        out_vec: Tuple[float, float],
        mode: str,
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        try:
            idx = int(axis)
            cur = int(frame)
            inx = float(in_vec[0])
            iny = float(in_vec[1])
            outx = float(out_vec[0])
            outy = float(out_vec[1])
        except Exception:
            return ((-3.0, 0.0), (3.0, 0.0))
        prev_frame, next_frame = self._timeline_axis_neighbor_frames(idx, cur)
        min_len = 0.05
        max_len = 8.0
        in_limit = max_len
        out_limit = max_len
        try:
            if prev_frame is not None:
                in_limit = min(float(max_len), max(float(min_len), float(cur - int(prev_frame)) * 0.49))
            if next_frame is not None:
                out_limit = min(float(max_len), max(float(min_len), float(int(next_frame) - cur) * 0.49))
        except Exception:
            in_limit = max_len
            out_limit = max_len
        if str(mode or "tied").strip().lower() != "untied":
            src_x = abs(float(outx))
            src_y = float(outy)
            if src_x <= 1.0e-6:
                src_x = abs(float(inx))
                src_y = -float(iny)
            lim = min(float(in_limit), float(out_limit))
            src_x = max(float(min_len), min(float(lim), float(src_x)))
            outx = float(src_x)
            outy = float(src_y)
            inx = -float(src_x)
            iny = -float(src_y)
        else:
            in_mag = abs(float(inx))
            out_mag = abs(float(outx))
            in_mag = max(float(min_len), min(float(in_limit), float(in_mag)))
            out_mag = max(float(min_len), min(float(out_limit), float(out_mag)))
            inx = -float(in_mag)
            outx = float(out_mag)
        return ((float(inx), float(iny)), (float(outx), float(outy)))

    def _timeline_entry_curve_handles_dict(self, entry, *, create: bool = False):
        if not isinstance(entry, dict):
            return None
        raw = entry.get("curve_handles", None)
        if isinstance(raw, dict):
            return raw
        if not bool(create):
            return None
        out = {}
        entry["curve_handles"] = out
        return out

    def _timeline_remove_entry_axis_handles(self, entry, axis: int) -> None:
        if not isinstance(entry, dict):
            return
        try:
            idx = int(axis)
        except Exception:
            return
        cmap = self._timeline_entry_curve_handles_dict(entry, create=False)
        if not isinstance(cmap, dict):
            return
        try:
            cmap.pop(str(idx), None)
        except Exception:
            pass
        if not cmap:
            try:
                entry.pop("curve_handles", None)
            except Exception:
                pass

    def _timeline_copy_entry_axis_handles(self, entry, axis: int):
        if not isinstance(entry, dict):
            return None
        try:
            idx = int(axis)
        except Exception:
            return None
        cmap = self._timeline_entry_curve_handles_dict(entry, create=False)
        if not isinstance(cmap, dict):
            return None
        raw = cmap.get(str(idx))
        if not isinstance(raw, dict):
            return None
        mode = str(raw.get("mode", "tied") or "tied").strip().lower()
        if mode == "straight":
            return {"mode": "straight"}
        mode = "untied" if mode == "untied" else "tied"
        in_raw = raw.get("in", None)
        out_raw = raw.get("out", None)
        if not (isinstance(in_raw, (list, tuple)) and len(in_raw) >= 2):
            return None
        if not (isinstance(out_raw, (list, tuple)) and len(out_raw) >= 2):
            return None
        try:
            return {
                "mode": mode,
                "in": [float(in_raw[0]), float(in_raw[1])],
                "out": [float(out_raw[0]), float(out_raw[1])],
            }
        except Exception:
            return None

    def _timeline_set_entry_axis_handles(
        self,
        entry,
        axis: int,
        frame: int,
        mode: str,
        in_vec: Tuple[float, float],
        out_vec: Tuple[float, float],
    ) -> Dict[str, object]:
        if not isinstance(entry, dict):
            return {"mode": "tied", "in": (-3.0, 0.0), "out": (3.0, 0.0)}
        try:
            idx = int(axis)
        except Exception:
            return {"mode": "tied", "in": (-3.0, 0.0), "out": (3.0, 0.0)}
        mode_raw = str(mode or "tied").strip().lower()
        mode_norm = "straight" if mode_raw == "straight" else ("untied" if mode_raw == "untied" else "tied")
        if mode_norm == "straight":
            cmap = self._timeline_entry_curve_handles_dict(entry, create=True)
            if not isinstance(cmap, dict):
                return {"mode": "straight", "in": (0.0, 0.0), "out": (0.0, 0.0)}
            cmap[str(idx)] = {"mode": "straight"}
            return {"mode": "straight", "in": (0.0, 0.0), "out": (0.0, 0.0)}
        in_v, out_v = self._timeline_clamp_axis_handle_pair(int(idx), int(frame), in_vec, out_vec, mode_norm)
        cmap = self._timeline_entry_curve_handles_dict(entry, create=True)
        if not isinstance(cmap, dict):
            return {"mode": mode_norm, "in": in_v, "out": out_v}
        cmap[str(idx)] = {
            "mode": str(mode_norm),
            "in": [float(in_v[0]), float(in_v[1])],
            "out": [float(out_v[0]), float(out_v[1])],
        }
        return {"mode": str(mode_norm), "in": in_v, "out": out_v}

    def _timeline_initialize_entry_axis_handle_if_missing(
        self,
        entry,
        axis: int,
        frame: int,
        *,
        key_value: Optional[float] = None,
    ) -> None:
        if not isinstance(entry, dict):
            return
        try:
            idx = int(axis)
            frm = int(frame)
        except Exception:
            return
        if idx < 0 or idx > 5:
            return
        if not self._timeline_axis_is_keyed(entry, idx):
            return
        has_valid = False
        cmap = self._timeline_entry_curve_handles_dict(entry, create=False)
        if isinstance(cmap, dict):
            raw = cmap.get(str(idx))
            if isinstance(raw, dict):
                mode_raw = str(raw.get("mode", "tied") or "tied").strip().lower()
                if mode_raw == "straight":
                    has_valid = True
                else:
                    in_raw = raw.get("in", None)
                    out_raw = raw.get("out", None)
                    has_valid = (
                        isinstance(in_raw, (list, tuple))
                        and len(in_raw) >= 2
                        and isinstance(out_raw, (list, tuple))
                        and len(out_raw) >= 2
                    )
        if bool(has_valid):
            return
        val = key_value
        if val is None:
            val = self._timeline_axis_value_for_entry(entry, idx)
        if val is not None:
            try:
                val = float(val)
            except Exception:
                val = None
        default_pair = self._timeline_default_axis_handles(idx, frm, key_value=val)
        try:
            mode = str(default_pair.get("mode", "tied") or "tied")
            in_v = tuple(default_pair.get("in", (-3.0, 0.0)))
            out_v = tuple(default_pair.get("out", (3.0, 0.0)))
            self._timeline_set_entry_axis_handles(
                entry,
                idx,
                frm,
                mode,
                (float(in_v[0]), float(in_v[1])),
                (float(out_v[0]), float(out_v[1])),
            )
        except Exception:
            pass

    def _timeline_entry_axis_handles(self, entry, axis: int, frame: int, *, create: bool = False):
        if not isinstance(entry, dict):
            return None
        try:
            idx = int(axis)
            frm = int(frame)
        except Exception:
            return None
        if not self._timeline_axis_is_keyed(entry, idx):
            return None
        mode = "tied"
        in_vec = None
        out_vec = None
        cmap = self._timeline_entry_curve_handles_dict(entry, create=False)
        if isinstance(cmap, dict):
            raw = cmap.get(str(idx))
            if isinstance(raw, dict):
                mode_raw = str(raw.get("mode", "tied") or "tied").strip().lower()
                if mode_raw == "straight":
                    mode = "straight"
                else:
                    mode = "untied" if mode_raw == "untied" else "tied"
                    in_raw = raw.get("in", None)
                    out_raw = raw.get("out", None)
                    if isinstance(in_raw, (list, tuple)) and len(in_raw) >= 2:
                        try:
                            in_vec = (float(in_raw[0]), float(in_raw[1]))
                        except Exception:
                            in_vec = None
                    if isinstance(out_raw, (list, tuple)) and len(out_raw) >= 2:
                        try:
                            out_vec = (float(out_raw[0]), float(out_raw[1]))
                        except Exception:
                            out_vec = None
        if mode == "straight":
            if create:
                return self._timeline_set_entry_axis_handles(
                    entry,
                    idx,
                    frm,
                    "straight",
                    (0.0, 0.0),
                    (0.0, 0.0),
                )
            return {"mode": "straight", "in": (0.0, 0.0), "out": (0.0, 0.0)}
        key_val = self._timeline_axis_value_for_entry(entry, idx)
        default_pair = self._timeline_default_axis_handles(idx, frm, key_value=key_val)
        if in_vec is None:
            in_vec = tuple(default_pair.get("in", (-3.0, 0.0)))
        if out_vec is None:
            out_vec = tuple(default_pair.get("out", (3.0, 0.0)))
        if create:
            return self._timeline_set_entry_axis_handles(entry, idx, frm, mode, in_vec, out_vec)
        in_v, out_v = self._timeline_clamp_axis_handle_pair(idx, frm, in_vec, out_vec, mode)
        return {"mode": mode, "in": in_v, "out": out_v}

    def _timeline_axis_handles_for_key(self, axis: int, frame: int, *, create: bool = False):
        try:
            idx = int(axis)
            frm = int(frame)
        except Exception:
            return None
        entry = (getattr(self, "_timeline_keys", {}) or {}).get(int(frm))
        if not isinstance(entry, dict):
            return None
        return self._timeline_entry_axis_handles(entry, idx, frm, create=bool(create))

    def _timeline_axis_handle_mode_for_key(self, axis: int, frame: int) -> str:
        h = self._timeline_axis_handles_for_key(axis, frame, create=False)
        if not isinstance(h, dict):
            return "tied"
        mode = str(h.get("mode", "tied") or "tied").strip().lower()
        if mode == "straight":
            return "straight"
        if mode == "untied":
            return "untied"
        return "tied"

    def _timeline_set_axis_handle_value(
        self,
        axis: int,
        frame: int,
        side: str,
        dx: float,
        dy: float,
        *,
        commit: bool = False,
    ) -> bool:
        try:
            idx = int(axis)
            frm = max(0, int(frame))
            side_norm = str(side or "").strip().lower()
            hx = float(dx)
            hy = float(dy)
        except Exception:
            return False
        if idx < 0 or idx > 5:
            return False
        if side_norm not in {"in", "out"}:
            return False
        keys = getattr(self, "_timeline_keys", {}) or {}
        entry = keys.get(int(frm))
        if not isinstance(entry, dict):
            return False
        if not self._timeline_axis_is_keyed(entry, idx):
            return False
        prev = self._timeline_entry_axis_handles(entry, idx, frm, create=True)
        if not isinstance(prev, dict):
            return False
        mode = str(prev.get("mode", "tied") or "tied").strip().lower()
        if mode not in {"tied", "untied", "straight"}:
            mode = "tied"
        if mode == "straight":
            mode = "tied"
        in_v = tuple(prev.get("in", (-3.0, 0.0)))
        out_v = tuple(prev.get("out", (3.0, 0.0)))
        if side_norm == "in":
            in_v = (float(hx), float(hy))
            if mode == "tied":
                out_v = (-float(hx), -float(hy))
        else:
            out_v = (float(hx), float(hy))
            if mode == "tied":
                in_v = (-float(hx), -float(hy))
        newv = self._timeline_set_entry_axis_handles(entry, idx, frm, mode, in_v, out_v)
        keys[int(frm)] = entry
        self._timeline_keys = keys
        changed = True
        try:
            old_in = tuple(prev.get("in", (0.0, 0.0)))
            old_out = tuple(prev.get("out", (0.0, 0.0)))
            new_in = tuple(newv.get("in", (0.0, 0.0)))
            new_out = tuple(newv.get("out", (0.0, 0.0)))
            old_mode = str(prev.get("mode", "tied") or "tied")
            new_mode = str(newv.get("mode", "tied") or "tied")
            changed = (
                old_mode != new_mode
                or abs(float(old_in[0]) - float(new_in[0])) > 1.0e-6
                or abs(float(old_in[1]) - float(new_in[1])) > 1.0e-6
                or abs(float(old_out[0]) - float(new_out[0])) > 1.0e-6
                or abs(float(old_out[1]) - float(new_out[1])) > 1.0e-6
            )
        except Exception:
            changed = True
        if not changed:
            return False
        entry["camera_state"] = {}
        if bool(commit):
            try:
                self._timeline_apply_frame_if_keyed(int(self._timeline_current_frame()), force=True)
            except Exception:
                pass
        canvas = getattr(self, "_timeline_curves_canvas", None)
        if canvas is not None:
            try:
                canvas.update()
            except Exception:
                pass
        if bool(commit):
            self._timeline_save_to_disk()
        return True

    def _timeline_set_selected_handles_mode(self, mode: str) -> None:
        mode_raw = str(mode or "").strip().lower()
        if mode_raw == "straight":
            mode_norm = "straight"
        elif mode_raw == "untied":
            mode_norm = "untied"
        else:
            mode_norm = "tied"
        try:
            selected = {
                (int(a), int(f))
                for (a, f) in (getattr(self, "_timeline_curve_selected", set()) or set())
            }
        except Exception:
            selected = set()
        if not selected:
            self._update_timeline_handle_mode_buttons()
            return
        keys = getattr(self, "_timeline_keys", {}) or {}
        applied = False
        for axis, frame in selected:
            entry = keys.get(int(frame))
            if not isinstance(entry, dict):
                continue
            if not self._timeline_axis_is_keyed(entry, int(axis)):
                continue
            key_val = self._timeline_axis_value_for_entry(entry, int(axis))
            if mode_norm == "straight":
                in_v = (0.0, 0.0)
                out_v = (0.0, 0.0)
            elif mode_norm == "tied":
                def_pair = self._timeline_default_axis_handles(
                    int(axis),
                    int(frame),
                    key_value=key_val,
                )
                in_v = tuple(def_pair.get("in", (-3.0, 0.0)))
                out_v = tuple(def_pair.get("out", (3.0, 0.0)))
            else:
                h = self._timeline_entry_axis_handles(entry, int(axis), int(frame), create=True)
                if not isinstance(h, dict):
                    continue
                in_v = tuple(h.get("in", (-3.0, 0.0)))
                out_v = tuple(h.get("out", (3.0, 0.0)))
            self._timeline_set_entry_axis_handles(
                entry,
                int(axis),
                int(frame),
                mode_norm,
                in_v,
                out_v,
            )
            entry["camera_state"] = {}
            keys[int(frame)] = entry
            applied = True
        if not applied:
            self._update_timeline_handle_mode_buttons()
            return
        self._timeline_keys = keys
        try:
            self._timeline_apply_frame_if_keyed(int(self._timeline_current_frame()), force=True)
        except Exception:
            pass
        self._timeline_save_to_disk()
        canvas = getattr(self, "_timeline_curves_canvas", None)
        if canvas is not None:
            try:
                canvas.update()
            except Exception:
                pass
        self._update_timeline_handle_mode_buttons()

    def _timeline_local_frame_to_tracks_x_float(
        self,
        local_frame: float,
        *,
        clamp: bool = True,
    ) -> Optional[float]:
        try:
            lf = float(local_frame)
        except Exception:
            return None
        slider = getattr(self, "_timeline_frame_slider", None)
        if slider is None:
            return None
        try:
            minv = int(slider.minimum())
            maxv = int(slider.maximum())
        except Exception:
            return None
        if maxv < minv:
            return None
        if bool(clamp):
            if lf < float(minv):
                lf = float(minv)
            if lf > float(maxv):
                lf = float(maxv)
        elif lf < float(minv) or lf > float(maxv):
            x_min = self._timeline_slider_to_tracks_x(int(minv))
            x_max = self._timeline_slider_to_tracks_x(int(maxv))
            step = None
            span_frames = int(maxv - minv)
            if span_frames > 0 and x_min is not None and x_max is not None:
                step = (float(x_max) - float(x_min)) / float(span_frames)
            if step is None:
                x0 = self._timeline_slider_to_tracks_x(int(minv))
                x1 = self._timeline_slider_to_tracks_x(int(minv + 1)) if int(minv + 1) <= int(maxv) else None
                if x0 is not None and x1 is not None:
                    step = float(x1) - float(x0)
            if step is None:
                tracks = getattr(self, "_timeline_tracks_frame", None)
                if tracks is not None:
                    try:
                        w = float(max(1, int(tracks.width())))
                        span = float(max(1, int(maxv) - int(minv)))
                        step = w / span
                    except Exception:
                        step = 1.0
                else:
                    step = 1.0
            if lf < float(minv):
                if x_min is None:
                    if x_max is None:
                        return None
                    x_min = float(x_max) - (float(step) * float(max(0, span_frames)))
                return float(x_min) + ((float(lf) - float(minv)) * float(step))
            if x_max is None:
                if x_min is None:
                    return None
                x_max = float(x_min) + (float(step) * float(max(0, span_frames)))
            return float(x_max) + ((float(lf) - float(maxv)) * float(step))
        lo = int(math.floor(lf))
        hi = int(math.ceil(lf))
        x_lo = self._timeline_slider_to_tracks_x(int(lo))
        if hi == lo:
            return float(x_lo) if x_lo is not None else None
        x_hi = self._timeline_slider_to_tracks_x(int(hi))
        if x_lo is None and x_hi is None:
            return None
        if x_lo is None:
            return float(x_hi)
        if x_hi is None:
            return float(x_lo)
        t = float(lf - float(lo)) / float(max(1, hi - lo))
        return float(x_lo) + ((float(x_hi) - float(x_lo)) * float(t))

    def _timeline_tracks_pixels_per_frame(self, local_frame: float) -> float:
        try:
            lf = float(local_frame)
        except Exception:
            lf = 0.0
        x0 = self._timeline_local_frame_to_tracks_x_float(float(lf))
        x1 = self._timeline_local_frame_to_tracks_x_float(float(lf) + 1.0)
        if x0 is not None and x1 is not None:
            step = abs(float(x1) - float(x0))
            if step > 1.0e-6:
                return float(step)
        tracks = getattr(self, "_timeline_tracks_frame", None)
        slider = getattr(self, "_timeline_frame_slider", None)
        if tracks is None or slider is None:
            return 1.0
        try:
            w = float(max(1, int(tracks.width())))
            span = float(max(1, int(slider.maximum()) - int(slider.minimum())))
            return max(1.0, w / span)
        except Exception:
            return 1.0

    def _timeline_eval_axis_curve(self, axis: int, frame: float) -> Optional[float]:
        try:
            idx = int(axis)
        except Exception:
            return None
        pts = self._timeline_axis_key_points(idx)
        if not pts:
            return None
        if len(pts) == 1:
            return float(pts[0][1])
        tframe = float(frame)
        if tframe <= float(pts[0][0]):
            return float(pts[0][1])
        if tframe >= float(pts[-1][0]):
            return float(pts[-1][1])
        for i in range(len(pts) - 1):
            f1, v1 = pts[i]
            f2, v2 = pts[i + 1]
            if tframe < float(f1) or tframe > float(f2):
                continue
            span = float(f2 - f1)
            if span <= 1e-6:
                return float(v2)
            u = (tframe - float(f1)) / span
            if u <= 0.0:
                return float(v1)
            if u >= 1.0:
                return float(v2)
            try:
                m1 = str(self._timeline_axis_handle_mode_for_key(int(idx), int(f1)) or "tied").strip().lower()
            except Exception:
                m1 = "tied"
            try:
                m2 = str(self._timeline_axis_handle_mode_for_key(int(idx), int(f2)) or "tied").strip().lower()
            except Exception:
                m2 = "tied"
            if m1 == "straight" or m2 == "straight":
                return float(v1) + ((float(v2) - float(v1)) * float(u))
            h1 = self._timeline_axis_handles_for_key(int(idx), int(f1), create=False) or self._timeline_default_axis_handles(
                int(idx),
                int(f1),
                key_value=float(v1),
            )
            h2 = self._timeline_axis_handles_for_key(int(idx), int(f2), create=False) or self._timeline_default_axis_handles(
                int(idx),
                int(f2),
                key_value=float(v2),
            )
            try:
                out_dx, out_dy = tuple(h1.get("out", (3.0, 0.0)))
            except Exception:
                out_dx, out_dy = (3.0, 0.0)
            try:
                in_dx, in_dy = tuple(h2.get("in", (-3.0, 0.0)))
            except Exception:
                in_dx, in_dy = (-3.0, 0.0)
            try:
                m1 = float(out_dy) / float(out_dx) if abs(float(out_dx)) > 1.0e-6 else 0.0
            except Exception:
                m1 = 0.0
            try:
                m2 = float(in_dy) / float(in_dx) if abs(float(in_dx)) > 1.0e-6 else 0.0
            except Exception:
                m2 = 0.0
            u2 = u * u
            u3 = u2 * u
            h00 = (2.0 * u3) - (3.0 * u2) + 1.0
            h10 = u3 - (2.0 * u2) + u
            h01 = (-2.0 * u3) + (3.0 * u2)
            h11 = u3 - u2
            return (
                (h00 * float(v1))
                + (h10 * span * float(m1))
                + (h01 * float(v2))
                + (h11 * span * float(m2))
            )
        return None

    def _timeline_eval_frame_values(self, frame: int) -> Tuple[Optional[Tuple[float, float, float]], Optional[Tuple[float, float, float]]]:
        try:
            f = int(frame)
        except Exception:
            f = 0
        vals: List[Optional[float]] = [self._timeline_eval_axis_curve(axis, f) for axis in range(6)]
        any_pos = any(vals[i] is not None for i in (0, 1, 2))
        any_rot = any(vals[i] is not None for i in (3, 4, 5))
        if not any_pos and not any_rot:
            return (None, None)
        xyz_out = None
        rxyz_out = None
        if any_pos:
            base_xyz = self._timeline_current_cam_xyz()
            if base_xyz is None:
                base_xyz = (0.0, 0.0, 0.0)
            xyz_out = (
                float(vals[0] if vals[0] is not None else base_xyz[0]),
                float(vals[1] if vals[1] is not None else base_xyz[1]),
                float(vals[2] if vals[2] is not None else base_xyz[2]),
            )
        if any_rot:
            base_rxyz = self._timeline_current_cam_rxyz()
            if base_rxyz is None:
                base_rxyz = (0.0, 0.0, 0.0)
            rxyz_out = (
                float(vals[3] if vals[3] is not None else base_rxyz[0]),
                float(vals[4] if vals[4] is not None else base_rxyz[1]),
                float(vals[5] if vals[5] is not None else base_rxyz[2]),
            )
        return (xyz_out, rxyz_out)

    def _timeline_slider_value_to_x(self, value: int, *, slider=None) -> Optional[int]:
        if slider is None:
            slider = getattr(self, "_timeline_frame_slider", None)
        if slider is None:
            return None
        try:
            v = int(value)
            min_frame = int(slider.minimum())
            max_frame = int(slider.maximum())
            if v < min_frame:
                v = min_frame
            if v > max_frame:
                v = max_frame
            opt = QtWidgets.QStyleOptionSlider()
            slider.initStyleOption(opt)
            opt.sliderPosition = int(v)
            opt.sliderValue = int(v)
            style = slider.style()
            handle = style.subControlRect(
                QtWidgets.QStyle.CC_Slider,
                opt,
                QtWidgets.QStyle.SC_SliderHandle,
                slider,
            )
            if handle is not None and int(handle.width()) > 0:
                return int(handle.center().x())
            groove = style.subControlRect(
                QtWidgets.QStyle.CC_Slider,
                opt,
                QtWidgets.QStyle.SC_SliderGroove,
                slider,
            )
            span = int(
                max(
                    1,
                    style.pixelMetric(
                        QtWidgets.QStyle.PM_SliderSpaceAvailable,
                        opt,
                        slider,
                    ),
                )
            )
            pos = int(
                QtWidgets.QStyle.sliderPositionFromValue(
                    min_frame,
                    max_frame,
                    v,
                    span,
                    bool(getattr(opt, "upsideDown", False)),
                )
            )
            slider_len = int(
                max(
                    1,
                    style.pixelMetric(
                        QtWidgets.QStyle.PM_SliderLength,
                        opt,
                        slider,
                    ),
                )
            )
            return int(groove.left() + pos + (slider_len // 2))
        except Exception:
            return None

    def _timeline_slider_to_tracks_x(self, local_frame: int) -> Optional[int]:
        tracks = getattr(self, "_timeline_tracks_frame", None)
        slider = getattr(self, "_timeline_frame_slider", None)
        if tracks is None or slider is None:
            return None
        try:
            x_slider = self._timeline_slider_value_to_x(int(local_frame), slider=slider)
            if x_slider is None:
                return None
            gx = slider.mapToGlobal(QtCore.QPoint(int(x_slider), 0))
            x = int(tracks.mapFromGlobal(gx).x())
            width = int(max(1, tracks.width()))
            return max(0, min(width - 1, int(x)))
        except Exception:
            return None

    def _timeline_tracks_x_to_slider_value(self, x_tracks: int) -> Optional[int]:
        tracks = getattr(self, "_timeline_tracks_frame", None)
        slider = getattr(self, "_timeline_frame_slider", None)
        if tracks is None or slider is None:
            return None
        try:
            gx = tracks.mapToGlobal(QtCore.QPoint(int(x_tracks), 0))
            sx = int(slider.mapFromGlobal(gx).x())
            opt = QtWidgets.QStyleOptionSlider()
            slider.initStyleOption(opt)
            style = slider.style()
            groove = style.subControlRect(
                QtWidgets.QStyle.CC_Slider,
                opt,
                QtWidgets.QStyle.SC_SliderGroove,
                slider,
            )
            span = int(
                max(
                    1,
                    style.pixelMetric(
                        QtWidgets.QStyle.PM_SliderSpaceAvailable,
                        opt,
                        slider,
                    ),
                )
            )
            slider_len = int(
                max(
                    1,
                    style.pixelMetric(
                        QtWidgets.QStyle.PM_SliderLength,
                        opt,
                        slider,
                    ),
                )
            )
            pos = int(round(float(sx) - float(groove.left()) - (float(slider_len) * 0.5)))
            pos = max(0, min(span, pos))
            value = int(
                QtWidgets.QStyle.sliderValueFromPosition(
                    int(slider.minimum()),
                    int(slider.maximum()),
                    int(pos),
                    int(span),
                    bool(getattr(opt, "upsideDown", False)),
                )
            )
            return max(int(slider.minimum()), min(int(slider.maximum()), value))
        except Exception:
            return None

    def _timeline_axis_color(self, axis: int) -> QtGui.QColor:
        colors = (
            QtGui.QColor("#ef4444"),
            QtGui.QColor("#22c55e"),
            QtGui.QColor("#3b82f6"),
            QtGui.QColor("#ef4444"),
            QtGui.QColor("#22c55e"),
            QtGui.QColor("#3b82f6"),
        )
        try:
            idx = int(axis)
        except Exception:
            idx = 0
        if idx < 0 or idx >= len(colors):
            idx = 0
        return colors[idx]

    def _timeline_axis_filter_set(self) -> set[int]:
        out: set[int] = set()
        try:
            raw = getattr(self, "_timeline_curve_axes_filter", set()) or set()
            for axis in raw:
                idx = int(axis)
                if 0 <= idx <= 5:
                    out.add(int(idx))
        except Exception:
            out = set()
        self._timeline_curve_axes_filter = set(out)
        return out

    def _timeline_axis_log(
        self,
        msg: str,
        *,
        throttle_key: str | None = None,
        interval: float = 0.0,
    ) -> None:
        # Keep logging code available for future debugging, but disable it by default.
        if not bool(getattr(self, "_timeline_axis_debug_logging", False)):
            return
        try:
            if throttle_key:
                now = time.time()
                last = float((getattr(self, "_timeline_axis_log_last", {}) or {}).get(str(throttle_key), 0.0))
                if float(interval) > 0.0 and (now - last) < float(interval):
                    return
                try:
                    self._timeline_axis_log_last[str(throttle_key)] = float(now)
                except Exception:
                    pass
            path = getattr(self, "_timeline_axis_log_path", None)
            if path is None:
                try:
                    root = Path(__file__).resolve().parents[2]
                    log_dir = root / "logs"
                except Exception:
                    log_dir = Path.cwd() / "logs"
                try:
                    log_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                path = log_dir / "timeline_axis_filter.log"
                self._timeline_axis_log_path = path
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with Path(path).open("a", encoding="utf-8") as f:
                f.write(f"[{ts}] {str(msg)}\n")
        except Exception:
            pass

    def _timeline_axis_is_visible(self, axis: int) -> bool:
        try:
            idx = int(axis)
        except Exception:
            return False
        if idx < 0 or idx > 5:
            return False
        filt = self._timeline_axis_filter_set()
        if not filt:
            return True
        return int(idx) in filt

    def _timeline_update_axis_label_styles(self) -> None:
        labels = getattr(self, "_timeline_axis_labels", None)
        if not isinstance(labels, list) or not labels:
            return
        filt = self._timeline_axis_filter_set()
        has_filter = bool(filt)
        for idx, lbl in enumerate(labels):
            if lbl is None:
                continue
            col = self._timeline_axis_color(int(idx))
            try:
                rr, gg, bb, _aa = col.getRgb()
            except Exception:
                rr, gg, bb = 226, 232, 240
            active = (not has_filter) or (int(idx) in filt)
            if has_filter and not active:
                text_col = "rgba(203,213,225,235)"
                bg_col = "transparent"
                border_col = "transparent"
            else:
                text_col = f"rgba({rr},{gg},{bb},255)"
                if has_filter:
                    bg_col = f"rgba({rr},{gg},{bb},38)"
                    border_col = f"rgba({rr},{gg},{bb},170)"
                else:
                    bg_col = "transparent"
                    border_col = "transparent"
            try:
                lbl.setStyleSheet(
                    f"color:{text_col};font-weight:700;padding:0px 4px;border-radius:3px;"
                    f"background:{bg_col};border:1px solid {border_col};"
                )
            except Exception:
                pass

    def _timeline_on_axis_label_clicked(self, axis: int, *, additive: bool = False) -> None:
        try:
            idx = int(axis)
        except Exception:
            return
        if idx < 0 or idx > 5:
            return
        self._timeline_axis_log(
            f"axis_click axis={int(idx)} additive={int(bool(additive))} before={sorted(list(self._timeline_axis_filter_set()))}"
        )
        filt = self._timeline_axis_filter_set()
        if bool(additive):
            if idx in filt:
                filt.discard(idx)
            else:
                filt.add(idx)
        else:
            if len(filt) == 1 and idx in filt:
                filt.clear()
            else:
                filt = {int(idx)}
        self._timeline_curve_axes_filter = {
            int(a)
            for a in filt
            if 0 <= int(a) <= 5
        }
        try:
            selected = {
                (int(a), int(f))
                for (a, f) in (getattr(self, "_timeline_curve_selected", set()) or set())
            }
        except Exception:
            selected = set()
        filt2 = self._timeline_axis_filter_set()
        if filt2:
            selected = {
                (int(a), int(f))
                for (a, f) in selected
                if int(a) in filt2
            }
        self._timeline_curve_selected = selected
        self._timeline_update_axis_label_styles()
        self._timeline_update_key_markers()
        canvas = getattr(self, "_timeline_curves_canvas", None)
        if canvas is not None:
            try:
                canvas.update()
            except Exception:
                pass
        self._timeline_axis_log(
            f"axis_click_done axis={int(idx)} additive={int(bool(additive))} after={sorted(list(self._timeline_axis_filter_set()))} "
            f"sel_count={int(len(getattr(self, '_timeline_curve_selected', set()) or set()))}"
        )

    def _timeline_on_axis_label_button_clicked(self, axis: int) -> None:
        try:
            idx = int(axis)
        except Exception:
            return
        if idx < 0 or idx > 5:
            return
        try:
            mods = QtWidgets.QApplication.keyboardModifiers()
        except Exception:
            mods = QtCore.Qt.NoModifier
        additive = bool(_qt_shift_active(mods))
        self._timeline_axis_log(
            f"axis_label_clicked_signal axis={int(idx)} additive={int(bool(additive))}"
        )
        self._timeline_on_axis_label_clicked(int(idx), additive=bool(additive))

    def _load_timeline_button_icons(self) -> None:
        if bool(getattr(self, "_timeline_icons_loaded", False)):
            return
        self._timeline_icons_loaded = True
        icon_play = None
        icon_stop = None
        icon_curve = None
        icon_curve_active = None
        icon_material_live = None
        icon_material_live_on = None
        icon_material_live_off = None
        icon_fx_switch = None
        icon_fx_switch_on = None
        icon_fx_switch_off = None
        icon_handle_straight = None
        icon_handle_tied = None
        icon_handle_untied = None
        icon_set_key = None
        icon_remove_key = None
        icon_loop = None
        icon_loop_on = None
        icon_loop_off = None
        keyframe_handle_path = None
        try:
            root = Path(__file__).resolve().parents[2]
            play_path = root / "icons" / "PlayButton_icon.png"
            stop_path = root / "icons" / "StopButton_icon.png"
            curve_path = root / "icons" / "CurveEditor_Icon.png"
            curve_active_path = root / "icons" / "CurveEditor_Active_Icon.png"
            material_live_on_path = root / "icons" / "LiveMaterial_Icon.png"
            material_live_off_path = root / "icons" / "LiveMaterial_Off_Icon.png"
            material_live_legacy_path = root / "icons" / "MaterialGenerator_Icon_S.png"
            fx_switch_on_path = root / "icons" / "fx_switch_On_icon.png"
            fx_switch_off_path = root / "icons" / "fx_switch_off_icon.png"
            handle_straight_path = root / "icons" / "StreightCurve_Icon.png"
            handle_tied_path = root / "icons" / "Tiehandles_Icon.png"
            handle_untied_path = root / "icons" / "Untiedhandle_Icon.png"
            set_key_path = root / "icons" / "keyframe_Icon.png"
            remove_key_path = root / "icons" / "RemoveKey_Icon.png"
            loop_on_path = root / "icons" / "Refresh_Icon.png"
            loop_off_path = root / "icons" / "StreightArrow_Icon.png"
            handle_path = root / "icons" / "KeyframeHandle_Icon.png"
            if play_path.exists():
                icon_play = QtGui.QIcon(str(play_path))
            if stop_path.exists():
                icon_stop = QtGui.QIcon(str(stop_path))
            if curve_path.exists():
                icon_curve = QtGui.QIcon(str(curve_path))
            if curve_active_path.exists():
                icon_curve_active = QtGui.QIcon(str(curve_active_path))
            if material_live_on_path.exists():
                icon_material_live_on = QtGui.QIcon(str(material_live_on_path))
            if material_live_off_path.exists():
                icon_material_live_off = QtGui.QIcon(str(material_live_off_path))
            if icon_material_live_on is None and material_live_legacy_path.exists():
                icon_material_live_on = QtGui.QIcon(str(material_live_legacy_path))
            if icon_material_live_off is None:
                icon_material_live_off = icon_material_live_on
            icon_material_live = icon_material_live_on
            if fx_switch_on_path.exists():
                icon_fx_switch_on = QtGui.QIcon(str(fx_switch_on_path))
            if fx_switch_off_path.exists():
                icon_fx_switch_off = QtGui.QIcon(str(fx_switch_off_path))
            if icon_fx_switch_on is None:
                icon_fx_switch_on = icon_fx_switch_off
            if icon_fx_switch_off is None:
                icon_fx_switch_off = icon_fx_switch_on
            icon_fx_switch = icon_fx_switch_on
            if handle_straight_path.exists():
                icon_handle_straight = QtGui.QIcon(str(handle_straight_path))
            if handle_tied_path.exists():
                icon_handle_tied = QtGui.QIcon(str(handle_tied_path))
            if handle_untied_path.exists():
                icon_handle_untied = QtGui.QIcon(str(handle_untied_path))
            if set_key_path.exists():
                icon_set_key = QtGui.QIcon(str(set_key_path))
            if remove_key_path.exists():
                icon_remove_key = QtGui.QIcon(str(remove_key_path))
            if loop_on_path.exists():
                icon_loop_on = QtGui.QIcon(str(loop_on_path))
            if loop_off_path.exists():
                icon_loop_off = QtGui.QIcon(str(loop_off_path))
            if icon_loop_on is None:
                icon_loop_on = icon_loop_off
            if icon_loop_off is None:
                icon_loop_off = icon_loop_on
            icon_loop = icon_loop_on
            if handle_path.exists():
                keyframe_handle_path = handle_path.as_posix()
        except Exception:
            icon_play = None
            icon_stop = None
            icon_curve = None
            icon_curve_active = None
            icon_material_live = None
            icon_material_live_on = None
            icon_material_live_off = None
            icon_fx_switch = None
            icon_fx_switch_on = None
            icon_fx_switch_off = None
            icon_handle_straight = None
            icon_handle_tied = None
            icon_handle_untied = None
            icon_set_key = None
            icon_remove_key = None
            icon_loop = None
            icon_loop_on = None
            icon_loop_off = None
            keyframe_handle_path = None
        self._timeline_icon_play = icon_play
        self._timeline_icon_stop = icon_stop
        self._timeline_icon_curve = icon_curve
        self._timeline_icon_curve_active = icon_curve_active
        self._timeline_icon_material_live = icon_material_live
        self._timeline_icon_material_live_on = icon_material_live_on
        self._timeline_icon_material_live_off = icon_material_live_off
        self._timeline_icon_fx_switch = icon_fx_switch
        self._timeline_icon_fx_switch_on = icon_fx_switch_on
        self._timeline_icon_fx_switch_off = icon_fx_switch_off
        self._timeline_icon_handle_straight = icon_handle_straight
        self._timeline_icon_handle_tied = icon_handle_tied
        self._timeline_icon_handle_untied = icon_handle_untied
        self._timeline_icon_set_key = icon_set_key
        self._timeline_icon_remove_key = icon_remove_key
        self._timeline_icon_loop = icon_loop
        self._timeline_icon_loop_on = icon_loop_on
        self._timeline_icon_loop_off = icon_loop_off
        self._timeline_keyframe_handle_path = keyframe_handle_path

    def _update_timeline_play_button(self) -> None:
        btn = getattr(self, "_timeline_play_btn", None)
        if btn is None:
            return
        self._load_timeline_button_icons()
        checked = bool(btn.isChecked())
        icon_play = getattr(self, "_timeline_icon_play", None)
        icon_stop = getattr(self, "_timeline_icon_stop", None)
        icon = icon_stop if checked else icon_play
        if icon is not None:
            try:
                btn.setIcon(icon)
                btn.setText("")
                inner = max(12, min(int(btn.width()), int(btn.height())) - 2)
                btn.setIconSize(QtCore.QSize(inner, inner))
            except Exception:
                pass
        else:
            try:
                btn.setIcon(QtGui.QIcon())
                btn.setText("Stop" if checked else "Play")
            except Exception:
                pass
        try:
            btn.setToolTip("Stop Playback" if checked else "Play Timeline")
        except Exception:
            pass

    @staticmethod
    def _timeline_marker_frame(value) -> Optional[int]:
        try:
            vv = int(value)
        except Exception:
            return None
        if vv < 0:
            return None
        return int(vv)

    def _timeline_in_out_frames(self) -> Tuple[Optional[int], Optional[int]]:
        in_frame = self._timeline_marker_frame(getattr(self, "_timeline_in_frame", None))
        out_frame = self._timeline_marker_frame(getattr(self, "_timeline_out_frame", None))
        if in_frame is not None and out_frame is not None and out_frame < in_frame:
            out_frame = in_frame
        self._timeline_in_frame = in_frame
        self._timeline_out_frame = out_frame
        return (in_frame, out_frame)

    def _timeline_loop_is_enabled(self) -> bool:
        return bool(getattr(self, "_timeline_loop_enabled", True))

    def _timeline_playback_bounds(self, *, max_frame: Optional[int] = None) -> Tuple[int, int]:
        if max_frame is None:
            try:
                max_frame = int(max(0, self._timeline_max_known_frame()))
            except Exception:
                max_frame = 0
        else:
            try:
                max_frame = int(max(0, int(max_frame)))
            except Exception:
                max_frame = 0
        in_frame, out_frame = self._timeline_in_out_frames()
        if in_frame is None:
            start = 0
        else:
            start = max(0, min(int(max_frame), int(in_frame)))
        if out_frame is None:
            end = int(max_frame)
        else:
            end = max(0, min(int(max_frame), int(out_frame)))
        if end < start:
            end = start
        return (int(start), int(end))

    def _timeline_stop_playback(self) -> None:
        btn = getattr(self, "_timeline_play_btn", None)
        if btn is not None:
            try:
                if bool(btn.isChecked()):
                    btn.setChecked(False)
                    return
            except Exception:
                pass
        try:
            self._timeline_play_timer.stop()
        except Exception:
            pass
        self._update_timeline_play_button()
        try:
            hook = getattr(self, "_timeline_audio_on_timeline_play_toggled", None)
            if callable(hook):
                hook(False)
        except Exception:
            pass

    def _timeline_update_range_marker_visuals(self) -> None:
        slider = getattr(self, "_timeline_frame_slider", None)
        if slider is not None:
            try:
                slider.update()
            except Exception:
                pass
        try:
            self._timeline_update_tick_labels()
        except Exception:
            pass
        try:
            self._timeline_update_playhead()
        except Exception:
            pass

    def _timeline_update_range_button_tooltips(self) -> None:
        in_frame, out_frame = self._timeline_in_out_frames()
        in_btn = getattr(self, "_timeline_mark_in_btn", None)
        if in_btn is not None:
            try:
                if in_frame is None:
                    in_btn.setToolTip("Set Start Frame ([)")
                else:
                    in_btn.setToolTip(f"Start Frame: {int(in_frame)} (click on same frame to clear)")
            except Exception:
                pass
        out_btn = getattr(self, "_timeline_mark_out_btn", None)
        if out_btn is not None:
            try:
                if out_frame is None:
                    out_btn.setToolTip("Set End Frame (])")
                else:
                    out_btn.setToolTip(f"End Frame: {int(out_frame)} (click on same frame to clear)")
            except Exception:
                pass

    def _update_timeline_loop_button(self) -> None:
        btn = getattr(self, "_timeline_loop_btn", None)
        if btn is None:
            return
        self._load_timeline_button_icons()
        enabled = self._timeline_loop_is_enabled()
        icon_on = getattr(self, "_timeline_icon_loop_on", None)
        icon_off = getattr(self, "_timeline_icon_loop_off", None)
        icon_loop = icon_on if enabled else icon_off
        if icon_loop is None:
            icon_loop = getattr(self, "_timeline_icon_loop", None)
        try:
            if bool(btn.isChecked()) != bool(enabled):
                btn.blockSignals(True)
                btn.setChecked(bool(enabled))
                btn.blockSignals(False)
        except Exception:
            try:
                btn.blockSignals(False)
            except Exception:
                pass
        if icon_loop is not None:
            try:
                btn.setIcon(icon_loop)
                btn.setText("")
                inner = max(12, min(int(btn.width()), int(btn.height())) - 4)
                btn.setIconSize(QtCore.QSize(inner, inner))
            except Exception:
                pass
        else:
            try:
                btn.setIcon(QtGui.QIcon())
                btn.setText("Loop")
            except Exception:
                pass
        try:
            btn.setToolTip("Loop Timeline Range" if enabled else "Loop Disabled")
        except Exception:
            pass

    def _timeline_set_loop_enabled(self, enabled: bool, *, save: bool = True) -> None:
        self._timeline_loop_enabled = bool(enabled)
        self._update_timeline_loop_button()
        if bool(save):
            try:
                self._timeline_range_save_to_disk()
            except Exception:
                pass

    def _timeline_on_loop_toggled(self, checked: bool) -> None:
        self._timeline_set_loop_enabled(bool(checked), save=True)
        if not bool(checked):
            try:
                _start, end = self._timeline_playback_bounds()
                cur = int(self._timeline_current_frame())
            except Exception:
                end = 0
                cur = 0
            if cur > int(end):
                self._timeline_set_frame_widgets(int(end))
                self._timeline_apply_frame_if_keyed(int(end), force=True)
                try:
                    self._timeline_apply_other_owner_frames(int(end))
                except Exception:
                    pass
            timer = getattr(self, "_timeline_play_timer", None)
            if timer is not None and timer.isActive() and int(cur) >= int(end):
                self._timeline_stop_playback()
        self._timeline_update_range_marker_visuals()

    def _timeline_on_mark_in_clicked(self) -> None:
        frame = max(0, int(self._timeline_current_frame()))
        in_frame, out_frame = self._timeline_in_out_frames()
        if in_frame is not None and int(in_frame) == int(frame):
            self._timeline_in_frame = None
        else:
            if out_frame is not None and int(frame) > int(out_frame):
                self._timeline_update_range_button_tooltips()
                return
            self._timeline_in_frame = int(frame)
            try:
                self._timeline_total_max = max(int(getattr(self, "_timeline_total_max", 240) or 240), int(frame))
            except Exception:
                pass
        self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=False)
        self._timeline_update_range_button_tooltips()
        self._timeline_update_range_marker_visuals()
        try:
            self._timeline_range_save_to_disk()
        except Exception:
            pass

    def _timeline_on_mark_out_clicked(self) -> None:
        frame = max(0, int(self._timeline_current_frame()))
        in_frame, out_frame = self._timeline_in_out_frames()
        if out_frame is not None and int(out_frame) == int(frame):
            self._timeline_out_frame = None
        else:
            if in_frame is not None and int(frame) < int(in_frame):
                self._timeline_update_range_button_tooltips()
                return
            self._timeline_out_frame = int(frame)
            try:
                self._timeline_total_max = max(int(getattr(self, "_timeline_total_max", 240) or 240), int(frame))
            except Exception:
                pass
        self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=False)
        self._timeline_update_range_button_tooltips()
        self._timeline_update_range_marker_visuals()
        try:
            self._timeline_range_save_to_disk()
        except Exception:
            pass

    def _update_timeline_curves_button(self) -> None:
        btn = getattr(self, "_timeline_curves_btn", None)
        if btn is None:
            return
        self._load_timeline_button_icons()
        mode = bool(getattr(self, "_timeline_curves_mode", False))
        icon_normal = getattr(self, "_timeline_icon_curve", None)
        icon_active = getattr(self, "_timeline_icon_curve_active", None)
        icon = icon_active if mode else icon_normal
        if icon is not None:
            try:
                btn.setIcon(icon)
                btn.setText("")
                inner = max(12, min(int(btn.width()), int(btn.height())) - 2)
                btn.setIconSize(QtCore.QSize(inner, inner))
            except Exception:
                pass
        else:
            try:
                btn.setIcon(QtGui.QIcon())
                btn.setText("Curves")
            except Exception:
                pass
        try:
            btn.setToolTip("Curve Editor")
        except Exception:
            pass

    def _timeline_material_live_enabled(self) -> bool:
        try:
            return bool(getattr(self, "_timeline_material_live_mode", True))
        except Exception:
            return True

    def _timeline_material_target_fps(self) -> float:
        fps = 0.0
        try:
            fps = float(getattr(self, "_timeline_material_fps_override", 0.0) or 0.0)
        except Exception:
            fps = 0.0
        if fps > 1.0:
            return fps
        try:
            fps = float(getattr(self, "_timeline_fps", 0.0) or 0.0)
        except Exception:
            fps = 0.0
        if fps > 1.0:
            return fps
        timer = getattr(self, "_timeline_play_timer", None)
        if timer is not None:
            try:
                interval = float(timer.interval())
                if interval > 0.0:
                    cand = 1000.0 / interval
                    if cand > 1.0:
                        return cand
            except Exception:
                pass
        return 30.0

    def _timeline_set_material_fps_override(self, fps: Optional[float]) -> None:
        val = 0.0
        try:
            if fps is not None:
                cand = float(fps)
                if cand > 1.0:
                    val = cand
        except Exception:
            val = 0.0
        self._timeline_material_fps_override = float(val)
        try:
            self._timeline_material_last_frame = None
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass

    def _timeline_material_timing(self, fallback_step: float) -> Tuple[float, Optional[float]]:
        try:
            step = max(0.0, float(fallback_step))
        except Exception:
            step = 0.0
        if self._timeline_material_live_enabled():
            try:
                self._timeline_material_last_frame = None
            except Exception:
                pass
            return (step, None)
        fps = max(1.0, float(self._timeline_material_target_fps()))
        try:
            frame = max(0, int(self._timeline_current_frame()))
        except Exception:
            frame = 0
        proc_time = float(frame) / float(fps)
        prev = getattr(self, "_timeline_material_last_frame", None)
        if isinstance(prev, int):
            delta = int(frame) - int(prev)
            step = float(delta) / float(fps) if delta > 0 else 0.0
        else:
            step = 0.0
        self._timeline_material_last_frame = int(frame)
        return (max(0.0, float(step)), max(0.0, float(proc_time)))

    def _timeline_set_material_live_mode(self, enabled: bool, *, sync_button: bool = True) -> None:
        live = bool(enabled)
        self._timeline_material_live_mode = live
        try:
            self._timeline_material_last_frame = None
        except Exception:
            pass
        btn = getattr(self, "_timeline_material_live_btn", None)
        if sync_button and btn is not None:
            try:
                btn.blockSignals(True)
                btn.setChecked(live)
            except Exception:
                pass
            finally:
                try:
                    btn.blockSignals(False)
                except Exception:
                    pass
        self._update_timeline_material_live_button()
        try:
            self.update()
        except Exception:
            pass

    def _timeline_on_material_live_toggled(self, checked: bool) -> None:
        self._timeline_set_material_live_mode(bool(checked), sync_button=False)

    def _timeline_fx_instances_are_enabled(self) -> bool:
        try:
            return bool(getattr(self, "_timeline_fx_instances_enabled", True))
        except Exception:
            return True

    def _timeline_set_fx_instances_enabled(self, enabled: bool, *, sync_button: bool = True) -> None:
        allow_instances = bool(enabled)
        self._timeline_fx_instances_enabled = allow_instances
        btn = getattr(self, "_timeline_fx_instances_btn", None)
        if sync_button and btn is not None:
            try:
                btn.blockSignals(True)
                btn.setChecked(bool(allow_instances))
            except Exception:
                pass
            finally:
                try:
                    btn.blockSignals(False)
                except Exception:
                    pass
        self._update_timeline_fx_instances_button()
        try:
            self.update()
        except Exception:
            pass

    def _timeline_on_fx_instances_toggled(self, checked: bool) -> None:
        self._timeline_set_fx_instances_enabled(bool(checked), sync_button=False)

    def _update_timeline_fx_instances_button(self) -> None:
        btn = getattr(self, "_timeline_fx_instances_btn", None)
        if btn is None:
            return
        self._load_timeline_button_icons()
        allow_instances = self._timeline_fx_instances_are_enabled()
        icon_on = getattr(self, "_timeline_icon_fx_switch_on", None)
        icon_off = getattr(self, "_timeline_icon_fx_switch_off", None)
        icon = icon_on if allow_instances else icon_off
        if icon is None:
            icon = getattr(self, "_timeline_icon_fx_switch", None)
        if icon is not None:
            try:
                btn.setIcon(icon)
                btn.setText("")
                inner = max(12, min(int(btn.width()), int(btn.height())) - 2)
                btn.setIconSize(QtCore.QSize(inner, inner))
            except Exception:
                pass
        else:
            try:
                btn.setIcon(QtGui.QIcon())
                btn.setText("FX" if allow_instances else "FX Off")
            except Exception:
                pass
        try:
            btn.blockSignals(True)
            btn.setChecked(bool(allow_instances))
        except Exception:
            pass
        finally:
            try:
                btn.blockSignals(False)
            except Exception:
                pass
        try:
            if allow_instances:
                btn.setToolTip("FX trail instance input enabled")
            else:
                btn.setToolTip("FX trail instance input disabled")
        except Exception:
            pass

    def _update_timeline_material_live_button(self) -> None:
        btn = getattr(self, "_timeline_material_live_btn", None)
        if btn is None:
            return
        self._load_timeline_button_icons()
        live = self._timeline_material_live_enabled()
        icon_on = getattr(self, "_timeline_icon_material_live_on", None)
        icon_off = getattr(self, "_timeline_icon_material_live_off", None)
        icon = icon_on if live else icon_off
        if icon is None:
            icon = getattr(self, "_timeline_icon_material_live", None)
        if icon is not None:
            try:
                btn.setIcon(icon)
                btn.setText("")
                inner = max(12, min(int(btn.width()), int(btn.height())) - 2)
                btn.setIconSize(QtCore.QSize(inner, inner))
            except Exception:
                pass
        else:
            try:
                btn.setIcon(QtGui.QIcon())
                btn.setText("L" if live else "T")
            except Exception:
                pass
        try:
            btn.blockSignals(True)
            btn.setChecked(bool(live))
        except Exception:
            pass
        finally:
            try:
                btn.blockSignals(False)
            except Exception:
                pass
        try:
            if live:
                btn.setToolTip("Live Material Time (Viewport FPS)")
            else:
                btn.setToolTip("Timeline Material Time (Frame Locked)")
        except Exception:
            pass

    def _timeline_selected_handle_mode(self) -> str:
        try:
            selected = {
                (int(a), int(f))
                for (a, f) in (getattr(self, "_timeline_curve_selected", set()) or set())
            }
        except Exception:
            selected = set()
        if not selected:
            return "tied"
        modes = set()
        for axis, frame in selected:
            h = self._timeline_axis_handles_for_key(int(axis), int(frame), create=False)
            if not isinstance(h, dict):
                continue
            mode = str(h.get("mode", "tied") or "tied").strip().lower()
            if mode == "straight":
                modes.add("straight")
            elif mode == "untied":
                modes.add("untied")
            else:
                modes.add("tied")
        if not modes:
            return "tied"
        if len(modes) == 1:
            return next(iter(modes))
        return "mixed"

    def _update_timeline_handle_mode_buttons(self) -> None:
        straight_btn = getattr(self, "_timeline_handle_straight_btn", None)
        tied_btn = getattr(self, "_timeline_handle_tied_btn", None)
        untied_btn = getattr(self, "_timeline_handle_untied_btn", None)
        if straight_btn is None and tied_btn is None and untied_btn is None:
            return
        self._load_timeline_button_icons()
        mode = self._timeline_selected_handle_mode()
        try:
            selected = {
                (int(a), int(f))
                for (a, f) in (getattr(self, "_timeline_curve_selected", set()) or set())
            }
        except Exception:
            selected = set()
        enabled = bool(getattr(self, "_timeline_curves_mode", False)) and bool(selected)

        icon_straight = getattr(self, "_timeline_icon_handle_straight", None)
        if straight_btn is not None:
            try:
                straight_btn.setEnabled(bool(enabled))
                if icon_straight is not None:
                    straight_btn.setIcon(icon_straight)
                    straight_btn.setText("")
                    inner = max(12, min(int(straight_btn.width()), int(straight_btn.height())) - 2)
                    straight_btn.setIconSize(QtCore.QSize(inner, inner))
                else:
                    straight_btn.setIcon(QtGui.QIcon())
                    straight_btn.setText("S")
                straight_btn.blockSignals(True)
                straight_btn.setChecked(mode == "straight")
                straight_btn.blockSignals(False)
                straight_btn.setToolTip("Set Selected Keys To Straight Segments")
            except Exception:
                pass

        icon_tied = getattr(self, "_timeline_icon_handle_tied", None)
        if tied_btn is not None:
            try:
                tied_btn.setEnabled(bool(enabled))
                if icon_tied is not None:
                    tied_btn.setIcon(icon_tied)
                    tied_btn.setText("")
                    inner = max(12, min(int(tied_btn.width()), int(tied_btn.height())) - 2)
                    tied_btn.setIconSize(QtCore.QSize(inner, inner))
                else:
                    tied_btn.setIcon(QtGui.QIcon())
                    tied_btn.setText("T")
                tied_btn.blockSignals(True)
                tied_btn.setChecked(mode == "tied")
                tied_btn.blockSignals(False)
                tied_btn.setToolTip("Set Selected Keys To Tied Handles")
            except Exception:
                pass

        icon_untied = getattr(self, "_timeline_icon_handle_untied", None)
        if untied_btn is not None:
            try:
                untied_btn.setEnabled(bool(enabled))
                if icon_untied is not None:
                    untied_btn.setIcon(icon_untied)
                    untied_btn.setText("")
                    inner = max(12, min(int(untied_btn.width()), int(untied_btn.height())) - 2)
                    untied_btn.setIconSize(QtCore.QSize(inner, inner))
                else:
                    untied_btn.setIcon(QtGui.QIcon())
                    untied_btn.setText("U")
                untied_btn.blockSignals(True)
                untied_btn.setChecked(mode == "untied")
                untied_btn.blockSignals(False)
                untied_btn.setToolTip("Set Selected Keys To Untied Handles")
            except Exception:
                pass

    def _timeline_update_handle_mode_buttons(self) -> None:
        self._update_timeline_handle_mode_buttons()

    def _timeline_on_handle_tied_clicked(self, _checked: bool = False) -> None:
        self._timeline_set_selected_handles_mode("tied")

    def _timeline_on_handle_straight_clicked(self, _checked: bool = False) -> None:
        self._timeline_set_selected_handles_mode("straight")

    def _timeline_on_handle_untied_clicked(self, _checked: bool = False) -> None:
        self._timeline_set_selected_handles_mode("untied")

    def _timeline_set_curves_mode(self, enabled: bool, *, sync_button: bool = True) -> None:
        mode = bool(enabled)
        self._timeline_curves_mode = mode
        btn = getattr(self, "_timeline_curves_btn", None)
        if sync_button and btn is not None:
            try:
                btn.blockSignals(True)
                btn.setChecked(mode)
            except Exception:
                pass
            finally:
                try:
                    btn.blockSignals(False)
                except Exception:
                    pass
        self._update_timeline_curves_button()
        stack = getattr(self, "_timeline_tracks_stack", None)
        if stack is not None:
            try:
                stack.setCurrentIndex(1 if mode else 0)
            except Exception:
                pass
        if mode:
            self._timeline_clear_key_markers()
        else:
            self._timeline_update_key_markers()
        canvas = getattr(self, "_timeline_curves_canvas", None)
        if canvas is not None:
            try:
                canvas.update()
            except Exception:
                pass
        self._update_timeline_handle_mode_buttons()
        self._timeline_update_playhead()

    def _timeline_on_curves_toggled(self, checked: bool) -> None:
        self._timeline_set_curves_mode(bool(checked), sync_button=False)

    def _timeline_drag_axis_key(
        self,
        axis: int,
        source_frame: int,
        target_frame: int,
        target_value: float,
        *,
        commit: bool = False,
    ) -> int:
        try:
            idx = int(axis)
            src = max(0, int(source_frame))
            dst = max(0, int(target_frame))
            val = float(target_value)
        except Exception:
            return int(source_frame) if isinstance(source_frame, int) else 0
        if idx < 0 or idx > 5:
            return src
        try:
            current_frame = int(self._timeline_current_frame())
        except Exception:
            current_frame = 0
        keys = getattr(self, "_timeline_keys", {}) or {}
        src_entry = keys.get(src)
        if not isinstance(src_entry, dict):
            return src
        if not self._timeline_axis_is_keyed(src_entry, idx):
            return src
        handle_copy = self._timeline_copy_entry_axis_handles(src_entry, idx)
        if dst != src:
            self._timeline_set_axis_keyed(src_entry, idx, False)
            src_entry["camera_state"] = {}
            if self._timeline_entry_has_any_axis(src_entry):
                keys[src] = src_entry
            else:
                try:
                    del keys[src]
                except Exception:
                    pass
            dst_entry = keys.get(dst)
            if not isinstance(dst_entry, dict):
                dst_entry = {}
            self._timeline_set_axis_value_for_entry(
                dst_entry,
                idx,
                val,
                frame=int(dst),
            )
            if isinstance(handle_copy, dict):
                try:
                    mode = str(handle_copy.get("mode", "tied") or "tied").strip().lower()
                    if mode == "straight":
                        self._timeline_set_entry_axis_handles(
                            dst_entry,
                            int(idx),
                            int(dst),
                            "straight",
                            (0.0, 0.0),
                            (0.0, 0.0),
                        )
                    else:
                        in_raw = handle_copy.get("in", (-3.0, 0.0))
                        out_raw = handle_copy.get("out", (3.0, 0.0))
                        self._timeline_set_entry_axis_handles(
                            dst_entry,
                            int(idx),
                            int(dst),
                            mode,
                            (float(in_raw[0]), float(in_raw[1])),
                            (float(out_raw[0]), float(out_raw[1])),
                        )
                except Exception:
                    pass
            dst_entry["camera_state"] = {}
            keys[dst] = dst_entry
            active_frame = dst
        else:
            self._timeline_set_axis_value_for_entry(
                src_entry,
                idx,
                val,
                frame=int(src),
            )
            src_entry["camera_state"] = {}
            keys[src] = src_entry
            active_frame = src
        self._timeline_keys = keys
        try:
            sel = getattr(self, "_timeline_curve_selected", set()) or set()
            old_item = (int(idx), int(src))
            new_item = (int(idx), int(active_frame))
            if old_item in sel:
                sel.discard(old_item)
                sel.add(new_item)
            self._timeline_curve_selected = {
                (int(a), int(f))
                for (a, f) in sel
            }
        except Exception:
            self._timeline_curve_selected = set()
        self._timeline_total_max = max(int(getattr(self, "_timeline_total_max", 240) or 240), int(active_frame))
        self._timeline_sync_range_controls(keep_current_visible=False, refresh_key_markers=True)
        if bool(commit):
            self._timeline_apply_frame_if_keyed(int(current_frame), force=True)
        canvas = getattr(self, "_timeline_curves_canvas", None)
        if canvas is not None:
            try:
                canvas.update()
            except Exception:
                pass
        if bool(commit):
            self._timeline_save_to_disk()
        return int(active_frame)

    def _timeline_drag_selected_keys(self, frame_delta: int, *, commit: bool = False) -> int:
        try:
            delta = int(frame_delta)
        except Exception:
            return 0
        if delta == 0:
            return 0
        try:
            current_frame = int(self._timeline_current_frame())
        except Exception:
            current_frame = 0
        try:
            selected = {
                (int(a), int(f))
                for (a, f) in (getattr(self, "_timeline_curve_selected", set()) or set())
            }
        except Exception:
            selected = set()
        if not selected:
            return 0

        keys = getattr(self, "_timeline_keys", {}) or {}
        moves: List[Tuple[int, int, float, Optional[dict]]] = []
        min_src = None
        for axis_raw, frame_raw in sorted(selected, key=lambda af: (int(af[1]), int(af[0]))):
            try:
                axis = int(axis_raw)
                src = max(0, int(frame_raw))
            except Exception:
                continue
            if axis < 0 or axis > 5:
                continue
            entry = keys.get(src)
            if not isinstance(entry, dict):
                continue
            if not self._timeline_axis_is_keyed(entry, axis):
                continue
            val = self._timeline_axis_value_for_entry(entry, axis)
            if val is None:
                continue
            hcopy = self._timeline_copy_entry_axis_handles(entry, int(axis))
            moves.append((int(axis), int(src), float(val), hcopy if isinstance(hcopy, dict) else None))
            if min_src is None or int(src) < int(min_src):
                min_src = int(src)
        if not moves:
            return 0

        applied = int(delta)
        if min_src is not None and (int(min_src) + int(applied)) < 0:
            applied = -int(min_src)
        if applied == 0:
            return 0

        for axis, src, _val, _hcopy in moves:
            entry = keys.get(int(src))
            if not isinstance(entry, dict):
                continue
            if not self._timeline_axis_is_keyed(entry, int(axis)):
                continue
            self._timeline_set_axis_keyed(entry, int(axis), False)
            entry["camera_state"] = {}
            if self._timeline_entry_has_any_axis(entry):
                keys[int(src)] = entry
            else:
                try:
                    del keys[int(src)]
                except Exception:
                    pass

        new_selected = set()
        max_frame = 0
        for axis, src, val, hcopy in moves:
            dst = max(0, int(src) + int(applied))
            dst_entry = keys.get(int(dst))
            if not isinstance(dst_entry, dict):
                dst_entry = {}
            self._timeline_set_axis_value_for_entry(
                dst_entry,
                int(axis),
                float(val),
                frame=int(dst),
            )
            if isinstance(hcopy, dict):
                try:
                    mode = str(hcopy.get("mode", "tied") or "tied").strip().lower()
                    if mode == "straight":
                        self._timeline_set_entry_axis_handles(
                            dst_entry,
                            int(axis),
                            int(dst),
                            "straight",
                            (0.0, 0.0),
                            (0.0, 0.0),
                        )
                    else:
                        in_raw = hcopy.get("in", (-3.0, 0.0))
                        out_raw = hcopy.get("out", (3.0, 0.0))
                        self._timeline_set_entry_axis_handles(
                            dst_entry,
                            int(axis),
                            int(dst),
                            mode,
                            (float(in_raw[0]), float(in_raw[1])),
                            (float(out_raw[0]), float(out_raw[1])),
                        )
                except Exception:
                    pass
            dst_entry["camera_state"] = {}
            keys[int(dst)] = dst_entry
            new_selected.add((int(axis), int(dst)))
            if int(dst) > int(max_frame):
                max_frame = int(dst)

        self._timeline_keys = keys
        self._timeline_curve_selected = {
            (int(a), int(f))
            for (a, f) in new_selected
        }
        self._timeline_total_max = max(int(getattr(self, "_timeline_total_max", 240) or 240), int(max_frame))
        self._timeline_sync_range_controls(keep_current_visible=False, refresh_key_markers=True)
        if bool(commit):
            self._timeline_apply_frame_if_keyed(int(current_frame), force=True)
        canvas = getattr(self, "_timeline_curves_canvas", None)
        if canvas is not None:
            try:
                canvas.update()
            except Exception:
                pass
        if bool(commit):
            self._timeline_save_to_disk()
        return int(applied)

    def _timeline_drag_selected_curve_keys(
        self,
        frame_delta: int,
        value_delta: float,
        *,
        commit: bool = False,
    ) -> Tuple[int, float]:
        try:
            delta_f = int(frame_delta)
            delta_v = float(value_delta)
        except Exception:
            return (0, 0.0)
        if delta_f == 0 and abs(delta_v) < 1.0e-9:
            return (0, 0.0)
        try:
            current_frame = int(self._timeline_current_frame())
        except Exception:
            current_frame = 0
        try:
            selected = {
                (int(a), int(f))
                for (a, f) in (getattr(self, "_timeline_curve_selected", set()) or set())
            }
        except Exception:
            selected = set()
        if not selected:
            return (0, 0.0)

        keys = getattr(self, "_timeline_keys", {}) or {}
        moves: List[Tuple[int, int, float, Optional[dict]]] = []
        min_src = None
        min_val = None
        max_val = None
        for axis_raw, frame_raw in sorted(selected, key=lambda af: (int(af[1]), int(af[0]))):
            try:
                axis = int(axis_raw)
                src = max(0, int(frame_raw))
            except Exception:
                continue
            if axis < 0 or axis > 5:
                continue
            entry = keys.get(src)
            if not isinstance(entry, dict):
                continue
            if not self._timeline_axis_is_keyed(entry, axis):
                continue
            val = self._timeline_axis_value_for_entry(entry, axis)
            if val is None:
                continue
            v = float(val)
            hcopy = self._timeline_copy_entry_axis_handles(entry, int(axis))
            moves.append((int(axis), int(src), v, hcopy if isinstance(hcopy, dict) else None))
            if min_src is None or int(src) < int(min_src):
                min_src = int(src)
            if min_val is None or v < float(min_val):
                min_val = v
            if max_val is None or v > float(max_val):
                max_val = v
        if not moves:
            return (0, 0.0)

        applied_f = int(delta_f)
        if min_src is not None and (int(min_src) + int(applied_f)) < 0:
            applied_f = -int(min_src)

        applied_v = float(delta_v)
        try:
            vmin = float(getattr(self, "_timeline_curve_min", -5.0))
            vmax = float(getattr(self, "_timeline_curve_max", 5.0))
            if vmax < vmin:
                vmin, vmax = vmax, vmin
            if min_val is not None and max_val is not None:
                low = float(vmin) - float(min_val)
                high = float(vmax) - float(max_val)
                if low > high:
                    applied_v = 0.0
                else:
                    applied_v = max(float(low), min(float(high), float(applied_v)))
        except Exception:
            pass

        if applied_f == 0 and abs(applied_v) < 1.0e-9:
            return (0, 0.0)

        for axis, src, _val, _hcopy in moves:
            entry = keys.get(int(src))
            if not isinstance(entry, dict):
                continue
            if not self._timeline_axis_is_keyed(entry, int(axis)):
                continue
            self._timeline_set_axis_keyed(entry, int(axis), False)
            entry["camera_state"] = {}
            if self._timeline_entry_has_any_axis(entry):
                keys[int(src)] = entry
            else:
                try:
                    del keys[int(src)]
                except Exception:
                    pass

        new_selected = set()
        max_frame = 0
        for axis, src, val, hcopy in moves:
            dst = max(0, int(src) + int(applied_f))
            dst_entry = keys.get(int(dst))
            if not isinstance(dst_entry, dict):
                dst_entry = {}
            self._timeline_set_axis_value_for_entry(
                dst_entry,
                int(axis),
                float(val + float(applied_v)),
                frame=int(dst),
            )
            if isinstance(hcopy, dict):
                try:
                    mode = str(hcopy.get("mode", "tied") or "tied").strip().lower()
                    if mode == "straight":
                        self._timeline_set_entry_axis_handles(
                            dst_entry,
                            int(axis),
                            int(dst),
                            "straight",
                            (0.0, 0.0),
                            (0.0, 0.0),
                        )
                    else:
                        in_raw = hcopy.get("in", (-3.0, 0.0))
                        out_raw = hcopy.get("out", (3.0, 0.0))
                        self._timeline_set_entry_axis_handles(
                            dst_entry,
                            int(axis),
                            int(dst),
                            mode,
                            (float(in_raw[0]), float(in_raw[1])),
                            (float(out_raw[0]), float(out_raw[1])),
                        )
                except Exception:
                    pass
            dst_entry["camera_state"] = {}
            keys[int(dst)] = dst_entry
            new_selected.add((int(axis), int(dst)))
            if int(dst) > int(max_frame):
                max_frame = int(dst)

        self._timeline_keys = keys
        self._timeline_curve_selected = {
            (int(a), int(f))
            for (a, f) in new_selected
        }
        self._timeline_total_max = max(int(getattr(self, "_timeline_total_max", 240) or 240), int(max_frame))
        self._timeline_sync_range_controls(keep_current_visible=False, refresh_key_markers=True)
        if bool(commit):
            self._timeline_apply_frame_if_keyed(int(current_frame), force=True)
        canvas = getattr(self, "_timeline_curves_canvas", None)
        if canvas is not None:
            try:
                canvas.update()
            except Exception:
                pass
        if bool(commit):
            self._timeline_save_to_disk()
        return (int(applied_f), float(applied_v))

    def _timeline_visible_row_key_points(self) -> List[Tuple[int, int, int, int, float]]:
        tracks = getattr(self, "_timeline_tracks_frame", None)
        slider = getattr(self, "_timeline_frame_slider", None)
        row_frames = getattr(self, "_timeline_track_rows", None)
        if tracks is None or slider is None or not isinstance(row_frames, list) or not row_frames:
            return []

        try:
            start = int(max(0, int(getattr(self, "_timeline_view_start", 0) or 0)))
        except Exception:
            start = 0
        try:
            local_max = int(max(0, int(slider.maximum())))
        except Exception:
            local_max = 0
        end = start + local_max

        keys = getattr(self, "_timeline_keys", {}) or {}
        try:
            key_items = sorted(keys.items(), key=lambda kv: int(kv[0]))
        except Exception:
            key_items = list(keys.items())

        out: List[Tuple[int, int, int, int, float]] = []
        for frame_raw, entry in key_items:
            try:
                frame = int(frame_raw)
            except Exception:
                continue
            if frame < start or frame > end:
                continue
            local = frame - start
            x = self._timeline_slider_to_tracks_x(local)
            if x is None:
                continue
            for axis in self._timeline_axes_for_entry(entry):
                if not bool(self._timeline_axis_is_visible(int(axis))):
                    continue
                if axis < 0 or axis >= len(row_frames):
                    continue
                row_frame = row_frames[axis]
                if row_frame is None:
                    continue
                val = self._timeline_axis_value_for_entry(entry, axis)
                if val is None:
                    continue
                try:
                    y = int(row_frame.y() + (row_frame.height() // 2))
                except Exception:
                    continue
                out.append((int(axis), int(frame), int(x), int(y), float(val)))
        return out

    def _timeline_hit_row_key(self, x_tracks: int, y_tracks: int, radius: int = 8) -> Optional[Tuple[int, int, float]]:
        try:
            px = float(x_tracks)
            py = float(y_tracks)
            rr = float(max(1, int(radius)))
        except Exception:
            return None
        best = None
        best_d2 = None
        for axis, frame, x, y, val in self._timeline_visible_row_key_points():
            dx = float(x) - px
            dy = float(y) - py
            d2 = (dx * dx) + (dy * dy)
            if d2 > (rr * rr):
                continue
            if best is None or best_d2 is None or d2 < best_d2:
                best = (int(axis), int(frame), float(val))
                best_d2 = d2
        return best

    def _timeline_keys_in_rows_rect(self, rectf) -> set[tuple[int, int]]:
        try:
            rr = QtCore.QRectF(rectf).normalized()
        except Exception:
            rr = QtCore.QRectF()
        out: set[tuple[int, int]] = set()
        if rr.isNull():
            return out
        for axis, frame, x, y, _val in self._timeline_visible_row_key_points():
            if rr.contains(QtCore.QPointF(float(x), float(y))):
                out.add((int(axis), int(frame)))
        return out

    def _timeline_update_key_markers(self) -> None:
        tracks = getattr(self, "_timeline_tracks_frame", None)
        slider = getattr(self, "_timeline_frame_slider", None)
        row_frames = getattr(self, "_timeline_track_rows", None)
        if bool(getattr(self, "_timeline_curves_mode", False)):
            self._timeline_clear_key_markers()
            canvas = getattr(self, "_timeline_curves_canvas", None)
            if canvas is not None:
                try:
                    canvas.update()
                except Exception:
                    pass
            try:
                self._timeline_update_handle_mode_buttons()
            except Exception:
                pass
            return
        if tracks is None or slider is None or not isinstance(row_frames, list) or not row_frames:
            self._timeline_clear_key_markers()
            return

        self._timeline_sync_row_alignment()
        self._timeline_clear_key_markers()
        try:
            selected = {
                (int(a), int(f))
                for (a, f) in (getattr(self, "_timeline_curve_selected", set()) or set())
            }
        except Exception:
            selected = set()

        per_row: List[List[QtWidgets.QFrame]] = [[] for _ in range(len(row_frames))]
        axis_fill = (
            "#ef4444",  # Px
            "#22c55e",  # Py
            "#3b82f6",  # Pz
            "#ef4444",  # Rx
            "#22c55e",  # Ry
            "#3b82f6",  # Rz
        )
        axis_border = (
            "#7f1d1d",
            "#14532d",
            "#1e3a8a",
            "#7f1d1d",
            "#14532d",
            "#1e3a8a",
        )
        dot_size = 8
        half = dot_size // 2

        for axis, frame, x, y, _val in self._timeline_visible_row_key_points():
            if axis < 0 or axis >= len(row_frames):
                continue
            dot = QtWidgets.QFrame(tracks)
            dot.setObjectName("GLTimelineKeyDot")
            dot.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            try:
                if (int(axis), int(frame)) in selected:
                    dot.setStyleSheet("background:#fde047;border:1px solid #f59e0b;border-radius:4px;")
                else:
                    dot.setStyleSheet(
                        f"background:{axis_fill[axis]};border:1px solid {axis_border[axis]};border-radius:4px;"
                    )
            except Exception:
                pass
            dot.setGeometry(int(x - half), int(y - half), dot_size, dot_size)
            dot.show()
            per_row[axis].append(dot)

        self._timeline_key_markers = per_row
        try:
            self._timeline_update_handle_mode_buttons()
        except Exception:
            pass

        playhead = getattr(self, "_timeline_playhead", None)
        if playhead is not None:
            try:
                playhead.raise_()
            except Exception:
                pass

    def _timeline_update_playhead(self) -> None:
        tracks = getattr(self, "_timeline_tracks_frame", None)
        playhead = getattr(self, "_timeline_playhead", None)
        slider = getattr(self, "_timeline_frame_slider", None)
        if tracks is None or playhead is None or slider is None:
            return
        self._timeline_sync_row_alignment()
        try:
            width = int(max(1, tracks.width()))
            height = int(max(1, tracks.height()))
            x = self._timeline_slider_to_tracks_x(int(slider.value()))
            if x is None:
                frame = int(slider.value()) - int(slider.minimum())
                max_frame = int(slider.maximum()) - int(slider.minimum())
                if frame < 0:
                    frame = 0
                if max_frame <= 0:
                    x = 0
                else:
                    x = int(round((float(frame) / float(max_frame)) * float(width - 1)))
            x = max(0, min(width - 1, int(x)))
            playhead.setGeometry(x, 0, 1, height)
            playhead.raise_()
            playhead.show()
        except Exception:
            pass

    def _timeline_on_scroll_changed(self, value: int) -> None:
        try:
            self._timeline_view_start = max(0, int(value))
        except Exception:
            self._timeline_view_start = 0
        self._timeline_sync_range_controls(keep_current_visible=False, refresh_key_markers=True)

    def _timeline_on_play_toggled(self, checked: bool) -> None:
        self._update_timeline_play_button()
        if bool(checked):
            try:
                max_frame = int(max(0, self._timeline_max_known_frame()))
            except Exception:
                max_frame = 0
            try:
                loop_enabled = self._timeline_loop_is_enabled()
            except Exception:
                loop_enabled = True
            start, end = self._timeline_playback_bounds(max_frame=max_frame)
            try:
                cur = int(self._timeline_current_frame())
            except Exception:
                cur = 0
            if not bool(loop_enabled) and int(cur) >= int(end):
                btn = getattr(self, "_timeline_play_btn", None)
                if btn is not None:
                    try:
                        btn.blockSignals(True)
                        btn.setChecked(False)
                    except Exception:
                        pass
                    finally:
                        try:
                            btn.blockSignals(False)
                        except Exception:
                            pass
                try:
                    self._timeline_play_timer.stop()
                except Exception:
                    pass
                self._update_timeline_play_button()
                try:
                    hook = getattr(self, "_timeline_audio_on_timeline_play_toggled", None)
                    if callable(hook):
                        hook(False)
                except Exception:
                    pass
                return
            if bool(loop_enabled) and (int(cur) < int(start) or int(cur) > int(end)):
                self._timeline_set_frame_widgets(int(start))
                self._timeline_apply_frame_if_keyed(int(start), force=True)
                try:
                    self._timeline_apply_other_owner_frames(int(start))
                except Exception:
                    pass
                try:
                    hook = getattr(self, "_timeline_audio_on_timeline_frame_changed", None)
                    if callable(hook):
                        hook(int(start), playing=False)
                except Exception:
                    pass
            try:
                self._timeline_play_timer.start()
            except Exception:
                pass
        else:
            try:
                self._timeline_play_timer.stop()
            except Exception:
                pass
        try:
            hook = getattr(self, "_timeline_audio_on_timeline_play_toggled", None)
            if callable(hook):
                hook(bool(checked))
        except Exception:
            pass

    def _timeline_on_play_tick(self) -> None:
        try:
            cur = int(self._timeline_current_frame())
        except Exception:
            cur = 0
        try:
            max_frame = int(max(0, self._timeline_max_known_frame()))
        except Exception:
            max_frame = 0
        try:
            loop_enabled = self._timeline_loop_is_enabled()
        except Exception:
            loop_enabled = True
        start, end = self._timeline_playback_bounds(max_frame=max_frame)
        frame = int(cur) + 1
        stop_after = False
        if bool(loop_enabled):
            if int(cur) < int(start) or int(cur) > int(end):
                frame = int(start)
            elif int(frame) > int(end):
                frame = int(start)
        else:
            if int(cur) >= int(end):
                self._timeline_stop_playback()
                return
            if int(frame) >= int(end):
                frame = int(end)
                stop_after = True
        self._timeline_set_frame_widgets(frame)
        self._timeline_apply_frame_if_keyed(frame, force=True)
        try:
            self._timeline_apply_other_owner_frames(frame)
        except Exception:
            pass
        try:
            hook = getattr(self, "_timeline_audio_on_timeline_frame_changed", None)
            if callable(hook):
                hook(int(frame), playing=True)
        except Exception:
            pass
        if bool(stop_after):
            self._timeline_stop_playback()

    def _timeline_refresh_coord_labels(self) -> None:
        try:
            if bool(getattr(self, "_timeline_enabled", False)):
                labels = getattr(self, "_timeline_axis_labels", None)
                if not (
                    isinstance(labels, list)
                    and len(labels) == 6
                    and all(isinstance(lb, QtWidgets.QPushButton) for lb in labels if lb is not None)
                ):
                    self._build_timeline_panel()
        except Exception:
            pass
        x_lbl = getattr(self, "_timeline_coord_x", None)
        y_lbl = getattr(self, "_timeline_coord_y", None)
        z_lbl = getattr(self, "_timeline_coord_z", None)
        rx_lbl = getattr(self, "_timeline_coord_rx", None)
        ry_lbl = getattr(self, "_timeline_coord_ry", None)
        rz_lbl = getattr(self, "_timeline_coord_rz", None)
        if x_lbl is None or y_lbl is None or z_lbl is None or rx_lbl is None or ry_lbl is None or rz_lbl is None:
            return
        xyz = None
        rxyz = None
        try:
            frame = int(self._timeline_current_frame())
            entry = (getattr(self, "_timeline_keys", {}) or {}).get(frame)
            if isinstance(entry, dict):
                vals = entry.get("xyz")
                if isinstance(vals, (list, tuple)) and len(vals) >= 3:
                    xyz = (float(vals[0]), float(vals[1]), float(vals[2]))
                rvals = entry.get("rxyz")
                if isinstance(rvals, (list, tuple)) and len(rvals) >= 3:
                    rxyz = (float(rvals[0]), float(rvals[1]), float(rvals[2]))
        except Exception:
            xyz = None
            rxyz = None
        if xyz is None:
            xyz = self._timeline_current_cam_xyz()
        if xyz is None:
            xyz = (0.0, 0.0, 0.0)
        if rxyz is None:
            rxyz = self._timeline_current_cam_rxyz()
        if rxyz is None:
            rxyz = (0.0, 0.0, 0.0)
        widgets = (x_lbl, y_lbl, z_lbl, rx_lbl, ry_lbl, rz_lbl)
        values = (xyz[0], xyz[1], xyz[2], rxyz[0], rxyz[1], rxyz[2])
        self._timeline_coord_syncing = True
        try:
            for ww, vv in zip(widgets, values):
                if ww is None:
                    continue
                try:
                    if ww.hasFocus():
                        continue
                except Exception:
                    pass
                try:
                    ww.blockSignals(True)
                except Exception:
                    pass
                try:
                    if hasattr(ww, "setValue"):
                        ww.setValue(float(vv))
                    else:
                        ww.setText(self._timeline_format_coord(vv))
                except Exception:
                    pass
                finally:
                    try:
                        ww.blockSignals(False)
                    except Exception:
                        pass
        finally:
            self._timeline_coord_syncing = False
        self._timeline_update_key_count_label()
        self._timeline_update_target_label()
        self._timeline_update_playhead()

    def _timeline_apply_owner_xyz_only(self, owner: str, xyz, rxyz=None) -> None:
        key = str(owner or "").strip()
        if not key:
            return
        if not isinstance(xyz, (list, tuple)) or len(xyz) < 3:
            return
        renderer = getattr(self, "_mgl_renderer", None) or self
        setf = getattr(renderer, "_mgl_set_scene_asset_xform", None)
        if not callable(setf):
            return
        try:
            pos = (float(xyz[0]), float(xyz[1]), float(xyz[2]))
        except Exception:
            return
        rot = None
        if isinstance(rxyz, (list, tuple)) and len(rxyz) >= 3:
            try:
                rot = (float(rxyz[0]), float(rxyz[1]), float(rxyz[2]))
            except Exception:
                rot = None
        is_splat = bool(self._timeline_owner_is_splat(key))
        try:
            if rot is None:
                setf(
                    key,
                    pos=pos,
                    apply_to_scene_models=not is_splat,
                    use_splat_xform=bool(is_splat),
                )
            else:
                setf(
                    key,
                    pos=pos,
                    rot=rot,
                    apply_to_scene_models=not is_splat,
                    use_splat_xform=bool(is_splat),
                )
        except Exception:
            return
        try:
            gizmo_owner = str(getattr(self, "_xform_gizmo_owner", "") or "").strip()
            if gizmo_owner.lower() == key.lower():
                self._xform_gizmo_pos_locked = False
                self._xform_gizmo_pos = pos
        except Exception:
            pass
        try:
            win = self.window()
            if win is not None and hasattr(win, "update_scene_asset_xform"):
                win.update_scene_asset_xform(key)
        except Exception:
            pass
        try:
            mode = str(getattr(self, "_camera_select_mode", "default") or "").strip()
            if mode and mode.lower() != "default" and mode.lower() == key.lower():
                sync_fn = getattr(self, "_sync_selected_scene_camera_view", None)
                if callable(sync_fn):
                    sync_fn(key)
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass

    def _timeline_apply_xyz_only(self, xyz, rxyz=None) -> None:
        owner = self._timeline_target_owner()
        if owner:
            self._timeline_apply_owner_xyz_only(owner, xyz, rxyz=rxyz)
            return
        if np is None:
            return
        if not isinstance(xyz, (list, tuple)) or len(xyz) < 3:
            return
        try:
            cam = getattr(self, "_fps_camera", None)
            if cam is None:
                self._fps_cam_sync_from_orbit()
                cam = getattr(self, "_fps_camera", None)
            if cam is None:
                cam = FpsCamera()
                self._fps_camera = cam
            cam.position = np.array([float(xyz[0]), float(xyz[1]), float(xyz[2])], dtype=np.float32)
            if isinstance(rxyz, (list, tuple)) and len(rxyz) >= 2:
                try:
                    pitch = math.radians(float(rxyz[0]))
                    yaw = math.radians(float(rxyz[1]))
                    cp = math.cos(pitch)
                    sp = math.sin(pitch)
                    sy = math.sin(yaw)
                    cy = math.cos(yaw)
                    fwd = np.array([sy * cp, sp, -cy * cp], dtype=np.float32)
                    fn = float(np.linalg.norm(fwd))
                    if fn > 1e-6:
                        cam.forward = (fwd / fn).astype(np.float32)
                        if hasattr(cam, "lock_roll"):
                            cam.lock_roll()
                except Exception:
                    pass
            if hasattr(cam, "_orthonormalize"):
                cam._orthonormalize()
            self._fps_camera = cam
            if bool(getattr(self, "_fly_mode_enabled", False)):
                self._fps_camera_active = True
            else:
                self._fps_cam_sync_orbit_from_camera()
                self._fps_camera_active = False
        except Exception:
            pass

    def _timeline_apply_frame_if_keyed(self, frame: int, *, force: bool = False) -> None:
        owner = self._timeline_target_owner()
        owner_key = self._timeline_owner_norm(owner)
        if owner_key:
            if bool(force):
                self._timeline_clear_manual_override(owner)
            else:
                try:
                    overrides = getattr(self, "_timeline_manual_override_owners", None)
                    if isinstance(overrides, set) and owner_key in overrides:
                        self._timeline_refresh_coord_labels()
                        return
                except Exception:
                    pass
        xyz_eval, rxyz_eval = self._timeline_eval_frame_values(int(frame))
        if xyz_eval is not None or rxyz_eval is not None:
            if xyz_eval is None:
                xyz_eval = self._timeline_current_cam_xyz()
            if xyz_eval is not None:
                self._timeline_apply_xyz_only(xyz_eval, rxyz_eval)
                try:
                    self.update()
                except Exception:
                    pass
                self._timeline_refresh_coord_labels()
                return
        try:
            entry = (getattr(self, "_timeline_keys", {}) or {}).get(int(frame))
        except Exception:
            entry = None
        if not isinstance(entry, dict) or not self._timeline_entry_has_any_axis(entry):
            self._timeline_refresh_coord_labels()
            return
        renderer = getattr(self, "_mgl_renderer", None) or self
        state = entry.get("camera_state", None)
        use_camera_state = not bool(self._timeline_target_owner())
        applied = False
        if use_camera_state and isinstance(state, dict) and state:
            try:
                apply_state = getattr(renderer, "_mgl_apply_camera_state", None)
                if callable(apply_state):
                    apply_state(dict(state))
                    applied = True
            except Exception:
                applied = False
        if not applied:
            self._timeline_apply_xyz_only(entry.get("xyz", None), entry.get("rxyz", None))
        try:
            self.update()
        except Exception:
            pass
        self._timeline_refresh_coord_labels()

    def _timeline_set_frame_widgets(self, frame: int) -> None:
        frame = max(0, int(frame))
        spin = getattr(self, "_timeline_frame_spin", None)
        if spin is not None:
            try:
                spin.blockSignals(True)
                spin.setValue(frame)
            except Exception:
                pass
            finally:
                try:
                    spin.blockSignals(False)
                except Exception:
                    pass
        self._timeline_sync_range_controls(keep_current_visible=True)

    def _timeline_on_frame_spin_changed(self, value: int) -> None:
        if bool(getattr(self, "_timeline_ignore_ui", False)):
            return
        frame = max(0, int(value))
        self._timeline_sync_range_controls(keep_current_visible=True)
        self._timeline_apply_frame_if_keyed(frame, force=True)
        try:
            self._timeline_apply_other_owner_frames(frame)
        except Exception:
            pass
        try:
            hook = getattr(self, "_timeline_audio_on_timeline_frame_changed", None)
            if callable(hook):
                hook(int(frame), playing=False)
        except Exception:
            pass

    def _timeline_on_frame_slider_changed(self, value: int) -> None:
        if bool(getattr(self, "_timeline_ignore_ui", False)):
            return
        try:
            start = int(getattr(self, "_timeline_view_start", 0) or 0)
        except Exception:
            start = 0
        frame = max(0, start + int(value))
        spin = getattr(self, "_timeline_frame_spin", None)
        if spin is not None:
            try:
                spin.blockSignals(True)
                spin.setValue(frame)
            except Exception:
                pass
            finally:
                try:
                    spin.blockSignals(False)
                except Exception:
                    pass
        self._timeline_apply_frame_if_keyed(frame, force=True)
        allow_aux_updates = True
        slider = getattr(self, "_timeline_frame_slider", None)
        if slider is not None:
            try:
                if bool(slider.isSliderDown()):
                    now_t = float(time.perf_counter())
                    last_t = float(getattr(self, "_timeline_scrub_aux_last_t", 0.0) or 0.0)
                    if (now_t - last_t) < 0.05:
                        allow_aux_updates = False
                    else:
                        self._timeline_scrub_aux_last_t = now_t
                else:
                    self._timeline_scrub_aux_last_t = 0.0
            except Exception:
                pass
        if not bool(allow_aux_updates):
            return
        try:
            self._timeline_apply_other_owner_frames(frame)
        except Exception:
            pass
        try:
            hook = getattr(self, "_timeline_audio_on_timeline_frame_changed", None)
            if callable(hook):
                hook(int(frame), playing=False)
        except Exception:
            pass

    def _timeline_on_set_key_clicked(self) -> None:
        frame = self._timeline_current_frame()
        xyz = self._timeline_current_cam_xyz()
        rxyz = self._timeline_current_cam_rxyz()
        if xyz is None:
            xyz = (0.0, 0.0, 0.0)
        if rxyz is None:
            rxyz = (0.0, 0.0, 0.0)
        state = self._timeline_capture_camera_state()
        keys = getattr(self, "_timeline_keys", {}) or {}
        entry = keys.get(int(frame))
        if not isinstance(entry, dict):
            entry = {}
        values = (
            float(xyz[0]),
            float(xyz[1]),
            float(xyz[2]),
            float(rxyz[0]),
            float(rxyz[1]),
            float(rxyz[2]),
        )
        for axis, value in enumerate(values):
            self._timeline_set_axis_value_for_entry(
                entry,
                int(axis),
                float(value),
                frame=int(frame),
            )
        entry["camera_state"] = state if isinstance(state, dict) else {}
        try:
            keys[int(frame)] = entry
            self._timeline_keys = keys
        except Exception:
            pass
        self._timeline_total_max = max(int(getattr(self, "_timeline_total_max", 240) or 240), int(frame))
        self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
        self._timeline_save_to_disk()
        self._timeline_refresh_coord_labels()

    def _timeline_on_delete_key_clicked(self) -> None:
        deleted_any = False
        selection_changed = False
        try:
            raw_sel = getattr(self, "_timeline_curve_selected", set()) or set()
            selected = {(int(a), int(f)) for (a, f) in raw_sel}
        except Exception:
            selected = set()

        if selected:
            keys = getattr(self, "_timeline_keys", {}) or {}
            by_frame: Dict[int, set[int]] = {}
            for axis, frame in selected:
                if int(axis) < 0 or int(axis) > 5:
                    continue
                if int(frame) < 0:
                    continue
                by_frame.setdefault(int(frame), set()).add(int(axis))

            for frame, axes in by_frame.items():
                entry = keys.get(int(frame))
                if not isinstance(entry, dict):
                    continue
                changed = False
                for axis in axes:
                    if self._timeline_axis_is_keyed(entry, int(axis)):
                        self._timeline_set_axis_keyed(entry, int(axis), False)
                        changed = True
                if not changed:
                    continue
                entry["camera_state"] = {}
                if self._timeline_entry_has_any_axis(entry):
                    keys[int(frame)] = entry
                else:
                    try:
                        del keys[int(frame)]
                    except Exception:
                        pass
                deleted_any = True

            self._timeline_keys = keys
            kept = {
                (int(axis), int(frame))
                for (axis, frame) in selected
                if isinstance((self._timeline_keys or {}).get(int(frame)), dict)
                and self._timeline_axis_is_keyed((self._timeline_keys or {}).get(int(frame)), int(axis))
            }
            selection_changed = kept != selected
            self._timeline_curve_selected = kept

        if not deleted_any and not selection_changed:
            return
        self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
        if deleted_any:
            self._timeline_save_to_disk()
        self._timeline_refresh_coord_labels()

    def _timeline_delete_selected_keys(self) -> bool:
        if not bool(getattr(self, "_timeline_enabled", False)):
            return False
        try:
            selected = getattr(self, "_timeline_curve_selected", set()) or set()
        except Exception:
            selected = set()
        if not bool(selected):
            return False
        try:
            self._timeline_on_delete_key_clicked()
            return True
        except Exception:
            return False

    # Public timeline actions used by shelf/controller integration.
    def timeline_has_selected_keys(self) -> bool:
        try:
            selected = getattr(self, "_timeline_curve_selected", set()) or set()
            return bool(selected)
        except Exception:
            return False

    def timeline_delete_selected_keys(self) -> bool:
        try:
            return bool(self._timeline_delete_selected_keys())
        except Exception:
            return False

    def timeline_set_key(self) -> bool:
        try:
            self._timeline_on_set_key_clicked()
            return True
        except Exception:
            return False

    def timeline_toggle_playback(self) -> bool:
        if not bool(getattr(self, "_timeline_enabled", False)):
            return False
        btn = getattr(self, "_timeline_play_btn", None)
        if btn is not None:
            try:
                btn.setChecked(not bool(btn.isChecked()))
                return True
            except Exception:
                pass
        try:
            timer = getattr(self, "_timeline_play_timer", None)
            playing = bool(timer is not None and timer.isActive())
        except Exception:
            playing = False
        try:
            self._timeline_on_play_toggled(not bool(playing))
            return True
        except Exception:
            return False

