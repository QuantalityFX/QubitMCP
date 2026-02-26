from __future__ import annotations

import json
import math
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
except Exception:
    np = None

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from echograph.ui.fps_camera import FpsCamera


def _qt_shift_active(mods) -> bool:
    try:
        return bool(mods & QtCore.Qt.ShiftModifier)
    except Exception:
        pass
    try:
        return bool(mods & QtCore.Qt.KeyboardModifier.ShiftModifier)
    except Exception:
        pass
    try:
        return bool(int(mods) & int(QtCore.Qt.ShiftModifier))
    except Exception:
        return False


class GraphGLTimelineMixin:
    @staticmethod
    def _timeline_safe_name(name: str) -> str:
        raw = str(name or "").strip()
        if not raw:
            return "scene"
        out = []
        for ch in raw:
            if ch.isalnum() or ch in ("_", "-"):
                out.append(ch)
            else:
                out.append("_")
        cleaned = "".join(out).strip("_")
        return cleaned or "scene"

    def _bottom_overlay_height(self) -> float:
        h = 0.0
        controls = getattr(self, "_controls", None)
        if controls is not None and controls.isVisible():
            h += float(getattr(self, "_controls_h", 0) or 0)
        panel = getattr(self, "_timeline_panel", None)
        if panel is not None and panel.isVisible():
            h += float(getattr(self, "_timeline_h", 0) or 0)
        return h

    def _timeline_current_frame(self) -> int:
        spin = getattr(self, "_timeline_frame_spin", None)
        if spin is None:
            return 0
        try:
            return int(spin.value())
        except Exception:
            return 0

    def _timeline_default_scene_name(self) -> str:
        try:
            win = self.window()
            active = getattr(win, "_active_scene_node", None) if win is not None else None
            nm = (getattr(active, "name", "") or "").strip() if active is not None else ""
            if nm:
                return nm
        except Exception:
            pass
        current = str(getattr(self, "_timeline_scene_name", "scene") or "scene").strip()
        return current or "scene"

    def _timeline_default_project_dir(self, project_path: str | None = None) -> Path:
        raw = (project_path or "").strip()
        if not raw:
            try:
                win = self.window()
                raw = str(getattr(win, "_current_path", "") or "").strip() if win is not None else ""
            except Exception:
                raw = ""
        if not raw:
            try:
                raw = str(getattr(getattr(self, "_scene", None), "_filename", "") or "").strip()
            except Exception:
                raw = ""
        if raw:
            try:
                p = Path(raw)
                if p.suffix:
                    p = p.parent
                return p
            except Exception:
                pass
        return Path(tempfile.gettempdir()) / "EchoGraph"

    def _timeline_anim_file_path(self, scene_name: str, project_path: str | None = None) -> Path:
        base_dir = self._timeline_default_project_dir(project_path=project_path)
        out_dir = base_dir / "projects"
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = self._timeline_safe_name(scene_name)
        return out_dir / f"{safe}_timeline.json"

    def _timeline_json_safe(self, value):
        if value is None:
            return None
        if isinstance(value, (str, bool)):
            return value
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            if math.isfinite(value):
                return float(value)
            return 0.0
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                out[str(k)] = self._timeline_json_safe(v)
            return out
        if isinstance(value, (list, tuple)):
            return [self._timeline_json_safe(v) for v in value]
        try:
            return float(value)
        except Exception:
            return str(value)

    def _timeline_capture_camera_state(self) -> dict:
        renderer = getattr(self, "_mgl_renderer", None) or self
        get_state = getattr(renderer, "_mgl_get_camera_state", None)
        if not callable(get_state):
            return {}
        try:
            state = get_state() or {}
        except Exception:
            state = {}
        if not isinstance(state, dict):
            return {}
        state = dict(state)
        try:
            state.pop("scene_xforms", None)
        except Exception:
            pass
        return self._timeline_json_safe(state)

    def _timeline_current_cam_xyz(self):
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
        self._timeline_set_axis_value_for_entry(entry, int(idx), float(value))
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
        try:
            cur = int(self._timeline_current_frame())
        except Exception:
            cur = 0
        try:
            base_total = int(getattr(self, "_timeline_total_max", 240) or 240)
        except Exception:
            base_total = 240
        return max(240, base_total, max_key, cur)

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

    def _timeline_set_axis_value_for_entry(self, entry, axis: int, value: float) -> None:
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

    def _timeline_eval_axis_curve(self, axis: int, frame: float) -> Optional[float]:
        pts = self._timeline_axis_key_points(axis)
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
            t = (tframe - float(f1)) / span
            p0 = float(pts[i - 1][1]) if i > 0 else float(v1)
            p1 = float(v1)
            p2 = float(v2)
            p3 = float(pts[i + 2][1]) if (i + 2) < len(pts) else float(v2)
            t2 = t * t
            t3 = t2 * t
            return 0.5 * (
                (2.0 * p1)
                + ((-p0 + p2) * t)
                + ((2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2)
                + ((-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)
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
        icon_set_key = None
        icon_remove_key = None
        keyframe_handle_path = None
        try:
            root = Path(__file__).resolve().parents[2]
            play_path = root / "icons" / "PlayButton_icon.png"
            stop_path = root / "icons" / "StopButton_icon.png"
            curve_path = root / "icons" / "CurveEditor_Icon.png"
            curve_active_path = root / "icons" / "CurveEditor_Active_Icon.png"
            set_key_path = root / "icons" / "keyframe_Icon.png"
            remove_key_path = root / "icons" / "RemoveKey_Icon.png"
            handle_path = root / "icons" / "KeyframeHandle_Icon.png"
            if play_path.exists():
                icon_play = QtGui.QIcon(str(play_path))
            if stop_path.exists():
                icon_stop = QtGui.QIcon(str(stop_path))
            if curve_path.exists():
                icon_curve = QtGui.QIcon(str(curve_path))
            if curve_active_path.exists():
                icon_curve_active = QtGui.QIcon(str(curve_active_path))
            if set_key_path.exists():
                icon_set_key = QtGui.QIcon(str(set_key_path))
            if remove_key_path.exists():
                icon_remove_key = QtGui.QIcon(str(remove_key_path))
            if handle_path.exists():
                keyframe_handle_path = handle_path.as_posix()
        except Exception:
            icon_play = None
            icon_stop = None
            icon_curve = None
            icon_curve_active = None
            icon_set_key = None
            icon_remove_key = None
            keyframe_handle_path = None
        self._timeline_icon_play = icon_play
        self._timeline_icon_stop = icon_stop
        self._timeline_icon_curve = icon_curve
        self._timeline_icon_curve_active = icon_curve_active
        self._timeline_icon_set_key = icon_set_key
        self._timeline_icon_remove_key = icon_remove_key
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
            self._timeline_set_axis_value_for_entry(dst_entry, idx, val)
            dst_entry["camera_state"] = {}
            keys[dst] = dst_entry
            active_frame = dst
        else:
            self._timeline_set_axis_value_for_entry(src_entry, idx, val)
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
        self._timeline_apply_frame_if_keyed(int(current_frame))
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
        moves: List[Tuple[int, int, float]] = []
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
            moves.append((int(axis), int(src), float(val)))
            if min_src is None or int(src) < int(min_src):
                min_src = int(src)
        if not moves:
            return 0

        applied = int(delta)
        if min_src is not None and (int(min_src) + int(applied)) < 0:
            applied = -int(min_src)
        if applied == 0:
            return 0

        for axis, src, _val in moves:
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
        for axis, src, val in moves:
            dst = max(0, int(src) + int(applied))
            dst_entry = keys.get(int(dst))
            if not isinstance(dst_entry, dict):
                dst_entry = {}
            self._timeline_set_axis_value_for_entry(dst_entry, int(axis), float(val))
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
        self._timeline_apply_frame_if_keyed(int(current_frame))
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
        moves: List[Tuple[int, int, float]] = []
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
            moves.append((int(axis), int(src), v))
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

        for axis, src, _val in moves:
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
        for axis, src, val in moves:
            dst = max(0, int(src) + int(applied_f))
            dst_entry = keys.get(int(dst))
            if not isinstance(dst_entry, dict):
                dst_entry = {}
            self._timeline_set_axis_value_for_entry(dst_entry, int(axis), float(val + float(applied_v)))
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
        self._timeline_apply_frame_if_keyed(int(current_frame))
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
                self._timeline_play_timer.start()
            except Exception:
                pass
        else:
            try:
                self._timeline_play_timer.stop()
            except Exception:
                pass

    def _timeline_on_play_tick(self) -> None:
        frame = int(self._timeline_current_frame()) + 1
        max_frame = int(self._timeline_max_known_frame())
        if frame > max_frame:
            frame = 0
        self._timeline_set_frame_widgets(frame)
        self._timeline_apply_frame_if_keyed(frame)

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
        self._timeline_update_playhead()

    def _timeline_save_to_disk(self) -> None:
        path = getattr(self, "_timeline_anim_path", None)
        if path is None:
            return
        keys_out = []
        try:
            items = sorted((getattr(self, "_timeline_keys", {}) or {}).items(), key=lambda kv: int(kv[0]))
        except Exception:
            items = []
        for frame, entry in items:
            if not isinstance(entry, dict):
                continue
            row = {"frame": int(frame)}
            xyz = entry.get("xyz", None)
            if isinstance(xyz, (list, tuple)) and len(xyz) >= 3:
                try:
                    row["xyz"] = [float(xyz[0]), float(xyz[1]), float(xyz[2])]
                except Exception:
                    pass
            rxyz = entry.get("rxyz", None)
            if isinstance(rxyz, (list, tuple)) and len(rxyz) >= 3:
                try:
                    row["rxyz"] = [float(rxyz[0]), float(rxyz[1]), float(rxyz[2])]
                except Exception:
                    pass
            st = entry.get("camera_state", None)
            if isinstance(st, dict) and st:
                row["camera_state"] = self._timeline_json_safe(st)
            mask = entry.get("axis_mask", None)
            if isinstance(mask, (list, tuple)) and len(mask) >= 6:
                try:
                    row["axis_mask"] = [bool(mask[i]) for i in range(6)]
                except Exception:
                    pass
            keys_out.append(row)
        payload = {
            "scene": str(getattr(self, "_timeline_scene_name", "scene") or "scene"),
            "fps": float(getattr(self, "_timeline_fps", 24.0) or 24.0),
            "keys": keys_out,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _timeline_load_from_disk(self) -> None:
        path = getattr(self, "_timeline_anim_path", None)
        self._timeline_keys = {}
        self._timeline_curve_selected = set()
        if path is None or not path.exists():
            self._timeline_total_max = max(240, int(self._timeline_current_frame()))
            self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
            self._timeline_update_key_count_label()
            self._timeline_refresh_coord_labels()
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        try:
            fps = float(raw.get("fps", 24.0))
            if fps > 0.0:
                self._timeline_fps = fps
        except Exception:
            self._timeline_fps = 24.0
        rows = raw.get("keys", []) or []
        data: Dict[int, Dict[str, object]] = {}
        for row in rows:
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
            if item:
                data[int(frame)] = item
        self._timeline_keys = data
        max_key = 0
        try:
            if data:
                max_key = max(int(k) for k in data.keys())
        except Exception:
            max_key = 0
        self._timeline_total_max = max(240, int(max_key))
        self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
        self._timeline_update_key_count_label()
        self._timeline_refresh_coord_labels()
        try:
            self._timeline_apply_frame_if_keyed(self._timeline_current_frame())
        except Exception:
            pass

    def set_timeline_scene_context(self, scene_name: str | None = None, project_path: str | None = None) -> None:
        name = str(scene_name or "").strip()
        if not name:
            name = self._timeline_default_scene_name()
        if not name:
            name = "scene"
        anim_path = self._timeline_anim_file_path(name, project_path=project_path)
        old_path = getattr(self, "_timeline_anim_path", None)
        same = old_path is not None and str(old_path) == str(anim_path)
        self._timeline_scene_name = name
        self._timeline_project_dir = anim_path.parent
        self._timeline_anim_path = anim_path
        if not same:
            self._timeline_load_from_disk()
        else:
            self._timeline_refresh_coord_labels()

    def _timeline_apply_xyz_only(self, xyz, rxyz=None) -> None:
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

    def _timeline_apply_frame_if_keyed(self, frame: int) -> None:
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
        applied = False
        if isinstance(state, dict) and state:
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
        self._timeline_apply_frame_if_keyed(frame)

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
        self._timeline_apply_frame_if_keyed(frame)

    def _timeline_on_set_key_clicked(self) -> None:
        frame = self._timeline_current_frame()
        xyz = self._timeline_current_cam_xyz()
        rxyz = self._timeline_current_cam_rxyz()
        if xyz is None:
            xyz = (0.0, 0.0, 0.0)
        if rxyz is None:
            rxyz = (0.0, 0.0, 0.0)
        state = self._timeline_capture_camera_state()
        entry = {
            "xyz": [float(xyz[0]), float(xyz[1]), float(xyz[2])],
            "rxyz": [float(rxyz[0]), float(rxyz[1]), float(rxyz[2])],
            "axis_mask": [True, True, True, True, True, True],
            "camera_state": state if isinstance(state, dict) else {},
        }
        try:
            self._timeline_keys[int(frame)] = entry
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

    def _layout_timeline_panel(self) -> None:
        panel = getattr(self, "_timeline_panel", None)
        if panel is None:
            return
        controls_h = 0
        controls = getattr(self, "_controls", None)
        if controls is not None and controls.isVisible():
            try:
                controls_h = int(getattr(self, "_controls_h", 44))
            except Exception:
                controls_h = 44
        pref_h = 0
        try:
            pref_h = int(panel.sizeHint().height())
        except Exception:
            pref_h = 0
        h = int(max(88, int(getattr(self, "_timeline_h", 72) or 72), pref_h))
        self._timeline_h = h
        y = max(0, self.height() - controls_h - h)
        panel.setGeometry(0, y, max(1, self.width()), h)
        panel.raise_()
        self._timeline_sync_row_alignment()
        self._timeline_update_key_markers()
        self._timeline_update_playhead()

    def _build_timeline_panel(self) -> None:
        existing = getattr(self, "_timeline_panel", None)
        if existing is not None:
            has_play = isinstance(getattr(self, "_timeline_play_btn", None), QtWidgets.QPushButton)
            has_curves_btn = isinstance(getattr(self, "_timeline_curves_btn", None), QtWidgets.QPushButton)
            has_scroll = getattr(self, "_timeline_scrollbar", None) is not None
            has_spacer = getattr(self, "_timeline_left_header_spacer", None) is not None
            has_rows = bool(getattr(self, "_timeline_track_rows", []))
            has_stack = getattr(self, "_timeline_tracks_stack", None) is not None
            has_canvas = getattr(self, "_timeline_curves_canvas", None) is not None
            has_axis_labels = (
                isinstance(getattr(self, "_timeline_axis_labels", None), list)
                and len(getattr(self, "_timeline_axis_labels", [])) == 6
                and all(
                    isinstance(lb, QtWidgets.QPushButton)
                    for lb in (getattr(self, "_timeline_axis_labels", []) or [])
                    if lb is not None
                )
            )
            if has_play and has_curves_btn and has_scroll and has_spacer and has_rows and has_stack and has_canvas and has_axis_labels:
                self._timeline_update_axis_label_styles()
                return
            try:
                existing.hide()
                existing.deleteLater()
            except Exception:
                pass
            self._timeline_panel = None
            self._timeline_tracks_frame = None
            self._timeline_playhead = None
            self._timeline_tracks_stack = None
            self._timeline_rows_host = None
            self._timeline_curves_canvas = None
            self._timeline_left_header_spacer = None
            self._timeline_area_widget = None
            self._timeline_axis_labels = []
            self._timeline_track_rows = []
            self._timeline_key_markers = []
            self._timeline_scrollbar = None
            self._timeline_play_btn = None
            self._timeline_curves_btn = None
            self._timeline_frame_slider = None
            self._timeline_frame_spin = None
            self._timeline_key_count_label = None
            self._timeline_tick_labels = []
            self._timeline_ticks_frame = None
        try:
            self._load_timeline_button_icons()
            slider_handle_css = (
                "#GLTimelinePanel QSlider#GLTimelineFrameSlider::handle:horizontal{"
                "background:#22c55e;border:1px solid #166534;width:24px;height:24px;margin:-11px 0;border-radius:12px;}"
            )
            handle_path = getattr(self, "_timeline_keyframe_handle_path", None)
            if isinstance(handle_path, str) and handle_path:
                handle_url = handle_path.replace("\\", "/").replace('"', '\\"')
                slider_handle_css = (
                    "#GLTimelinePanel QSlider#GLTimelineFrameSlider::handle:horizontal{"
                    + "image:url(\""
                    + handle_url
                    + "\");background:transparent;border:0px;width:24px;height:24px;margin:-11px 0;}"
                )
            panel = QtWidgets.QFrame(self)
            panel.setObjectName("GLTimelinePanel")
            panel.setAttribute(QtCore.Qt.WA_StyledBackground, True)
            panel.setStyleSheet(
                "".join((
                    "#GLTimelinePanel{background:rgba(15,23,42,215);border-top:1px solid #334155;}",
                    "#GLTimelinePanel QLabel{color:#e2e8f0;font-size:11px;}",
                    "#GLTimelinePanel QSpinBox,#GLTimelinePanel QDoubleSpinBox{background:#0f1216;color:#e2e8f0;border:1px solid #334155;border-radius:3px;padding:1px 4px;}",
                    "#GLTimelinePanel QSlider#GLTimelineFrameSlider::groove:horizontal{height:2px;background:#334155;border-radius:1px;}",
                    "#GLTimelinePanel QSlider#GLTimelineFrameSlider::sub-page:horizontal{background:#334155;border-radius:1px;}",
                    "#GLTimelinePanel QSlider#GLTimelineFrameSlider::add-page:horizontal{background:#334155;border-radius:1px;}",
                    slider_handle_css,
                    "#GLTimelinePanel QPushButton{padding:2px 8px;font-weight:600;color:#e2e8f0;background:#1f2937;border-radius:4px;}",
                    "#GLTimelinePanel QPushButton:hover{background:#334155;}",
                    "#GLTimelinePanel QPushButton#GLTimelinePlayButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelinePlayButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelinePlayButton:checked{background:transparent;border:0px;}",
                    "#GLTimelinePanel QFrame#GLTimelineTracks{background:rgba(15,18,22,120);border:1px solid #334155;border-radius:4px;}",
                    "#GLTimelinePanel QFrame#GLTimelineTrackRow{background:rgba(15,18,22,34);border-radius:3px;}",
                    "#GLTimelinePanel QFrame#GLTimelineTrackLine{background:rgba(148,163,184,80);border:0px;}",
                    "#GLTimelinePanel QFrame#GLTimelineKeyDot{background:#ef4444;border:1px solid #991b1b;border-radius:4px;}",
                    "#GLTimelinePanel QFrame#GLTimelinePlayhead{background:#ffffff;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineCurvesButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineCurvesButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineCurvesButton:checked{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineAxisButton{padding:0px 4px;text-align:left;background:transparent;border:0px;border-radius:3px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineSetKeyButton{padding:0px;background:rgba(0,0,0,220);border:1px solid rgba(226,232,240,215);border-radius:15px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineSetKeyButton:hover{background:rgba(34,211,238,65);border:1px solid rgba(34,211,238,240);}",
                    "#GLTimelinePanel QPushButton#GLTimelineSetKeyButton:pressed{background:rgba(34,211,238,90);border:1px solid rgba(125,211,252,255);}",
                    "#GLTimelinePanel QPushButton#GLTimelineDeleteKeyButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineDeleteKeyButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QWidget#GLTimelineCurveCanvas{background:rgba(15,18,22,34);border-radius:3px;}",
                    "#GLTimelinePanel QDoubleSpinBox#GLTimelineValue{color:#f8fafc;}",
                    "#GLTimelinePanel QFrame#GLTimelineTicks QLabel{color:#94a3b8;font-size:10px;}",
                    "#GLTimelinePanel QScrollBar:horizontal{background:rgba(15,18,22,90);height:10px;border:1px solid rgba(51,65,85,150);border-radius:4px;}",
                    "#GLTimelinePanel QScrollBar::handle:horizontal{background:rgba(148,163,184,170);min-width:30px;border-radius:4px;}",
                    "#GLTimelinePanel QScrollBar::add-line:horizontal,#GLTimelinePanel QScrollBar::sub-line:horizontal{width:0px;height:0px;}",
                    "#GLTimelinePanel QScrollBar::add-page:horizontal,#GLTimelinePanel QScrollBar::sub-page:horizontal{background:transparent;}",
                ))
            )
            root = QtWidgets.QVBoxLayout(panel)
            root.setContentsMargins(10, 6, 10, 8)
            root.setSpacing(6)

            header = QtWidgets.QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(8)

            frame_lbl = QtWidgets.QLabel("Frame")
            header.addWidget(frame_lbl, 0)

            frame_spin = QtWidgets.QSpinBox(panel)
            frame_spin.setRange(0, 100000)
            frame_spin.setValue(0)
            frame_spin.setFixedWidth(96)
            frame_spin.valueChanged.connect(self._timeline_on_frame_spin_changed)
            header.addWidget(frame_spin, 0)
            self._timeline_frame_spin = frame_spin

            play_btn = QtWidgets.QPushButton(panel)
            play_btn.setObjectName("GLTimelinePlayButton")
            play_btn.setText("Play")
            play_btn.setCheckable(True)
            play_btn.setFixedSize(26, 26)
            play_btn.setFlat(True)
            play_btn.toggled.connect(self._timeline_on_play_toggled)
            header.addWidget(play_btn, 0)
            self._timeline_play_btn = play_btn
            self._update_timeline_play_button()

            curves_btn = QtWidgets.QPushButton("Curves", panel)
            curves_btn.setObjectName("GLTimelineCurvesButton")
            curves_btn.setCheckable(True)
            curves_btn.setFixedSize(26, 26)
            curves_btn.setFlat(True)
            curves_btn.toggled.connect(self._timeline_on_curves_toggled)
            header.addWidget(curves_btn, 0)
            self._timeline_curves_btn = curves_btn
            self._update_timeline_curves_button()

            del_btn = QtWidgets.QPushButton(panel)
            del_btn.setObjectName("GLTimelineDeleteKeyButton")
            del_btn.setText("")
            del_btn.setFixedSize(30, 30)
            del_btn.setFlat(True)
            del_btn.setToolTip("Delete Selected Keys")
            del_icon = getattr(self, "_timeline_icon_remove_key", None)
            if del_icon is not None:
                try:
                    del_btn.setIcon(del_icon)
                    del_btn.setIconSize(QtCore.QSize(22, 22))
                except Exception:
                    pass
            del_btn.clicked.connect(self._timeline_on_delete_key_clicked)
            header.addWidget(del_btn, 0)
            self._timeline_key_count_label = None

            header.addStretch(1)

            key_btn = QtWidgets.QPushButton(panel)
            key_btn.setObjectName("GLTimelineSetKeyButton")
            key_btn.setText("")
            key_btn.setFixedSize(30, 30)
            key_btn.setFlat(True)
            key_btn.setToolTip("Set Key")
            key_icon = getattr(self, "_timeline_icon_set_key", None)
            if key_icon is not None:
                try:
                    key_btn.setIcon(key_icon)
                    key_btn.setIconSize(QtCore.QSize(22, 22))
                except Exception:
                    pass
            key_btn.clicked.connect(self._timeline_on_set_key_clicked)
            header.addWidget(key_btn, 0)
            root.addLayout(header, 0)

            tracks_grid = QtWidgets.QGridLayout()
            tracks_grid.setContentsMargins(0, 0, 0, 0)
            tracks_grid.setHorizontalSpacing(8)
            tracks_grid.setVerticalSpacing(4)

            channels = (
                ("Px", "_timeline_coord_x", "#ef4444"),
                ("Py", "_timeline_coord_y", "#22c55e"),
                ("Pz", "_timeline_coord_z", "#3b82f6"),
                ("Rx", "_timeline_coord_rx", "#ef4444"),
                ("Ry", "_timeline_coord_ry", "#22c55e"),
                ("Rz", "_timeline_coord_rz", "#3b82f6"),
            )

            left_header_spacer = QtWidgets.QWidget(panel)
            left_header_spacer.setFixedHeight(34)
            tracks_grid.addWidget(left_header_spacer, 0, 0, 1, 2)
            self._timeline_left_header_spacer = left_header_spacer
            self._timeline_axis_labels = []

            class _TimelineAxisLabel(QtWidgets.QPushButton):
                def __init__(self, view, axis: int, text: str, parent=None):
                    super().__init__(text, parent)
                    self._view = view
                    self._axis = int(axis)
                    self.setCursor(QtCore.Qt.PointingHandCursor)
                    self.setObjectName("GLTimelineAxisButton")
                    self.setFlat(True)
                    self.setFocusPolicy(QtCore.Qt.NoFocus)

                def mousePressEvent(self, ev):
                    if ev.button() == QtCore.Qt.LeftButton:
                        try:
                            mods = ev.modifiers()
                        except Exception:
                            mods = QtCore.Qt.NoModifier
                        additive = bool(_qt_shift_active(mods))
                        try:
                            self._view._timeline_axis_log(
                                f"axis_label_mouse axis={int(self._axis)} additive={int(bool(additive))}"
                            )
                        except Exception:
                            pass
                        try:
                            self._view._timeline_on_axis_label_clicked(int(self._axis), additive=bool(additive))
                        except Exception:
                            pass
                        try:
                            ev.accept()
                        except Exception:
                            pass
                        return
                    return super().mousePressEvent(ev)

            for row, (label_text, attr_name, color_hex) in enumerate(channels, start=1):
                axis_idx = int(row - 1)
                ch_lbl = _TimelineAxisLabel(self, axis_idx, label_text, panel)
                ch_lbl.setFixedHeight(22)
                try:
                    ch_lbl.clicked.connect(
                        lambda _checked=False, axis=axis_idx: self._timeline_on_axis_label_button_clicked(int(axis))
                    )
                except Exception:
                    pass
                tracks_grid.addWidget(ch_lbl, row, 0, 1, 1)
                tracks_grid.setRowMinimumHeight(row, 22)
                self._timeline_axis_labels.append(ch_lbl)

                val_input = QtWidgets.QDoubleSpinBox(panel)
                val_input.setObjectName("GLTimelineValue")
                val_input.setDecimals(3)
                val_input.setRange(-1000000.0, 1000000.0)
                val_input.setSingleStep(0.1)
                val_input.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
                val_input.setKeyboardTracking(False)
                val_input.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                val_input.setFixedHeight(22)
                val_input.setMinimumWidth(86)
                val_input.setValue(0.0)
                val_input.setStyleSheet(f"color:{color_hex};font-weight:700;")
                val_input.editingFinished.connect(
                    lambda axis=axis_idx, widget=val_input: self._timeline_on_coord_input_committed(axis, widget)
                )
                tracks_grid.addWidget(val_input, row, 1, 1, 1)
                setattr(self, attr_name, val_input)

            self._timeline_update_axis_label_styles()
            self._timeline_axis_log("timeline_panel_ready axis_buttons=6")

            timeline_area = QtWidgets.QWidget(panel)
            self._timeline_area_widget = timeline_area
            timeline_area_layout = QtWidgets.QVBoxLayout(timeline_area)
            timeline_area_layout.setContentsMargins(0, 0, 0, 0)
            timeline_area_layout.setSpacing(4)

            ticks_frame = QtWidgets.QFrame(timeline_area)
            ticks_frame.setObjectName("GLTimelineTicks")
            ticks_frame.setFixedHeight(16)
            self._timeline_ticks_frame = ticks_frame
            self._timeline_tick_labels = []
            timeline_area_layout.addWidget(ticks_frame, 0)

            class _TimelineFrameSlider(QtWidgets.QSlider):
                def __init__(self, view, parent=None):
                    super().__init__(QtCore.Qt.Horizontal, parent)
                    self._view = view
                    self._jump_drag_active = False
                    self.setMouseTracking(True)

                @staticmethod
                def _event_pos(ev) -> QtCore.QPoint:
                    try:
                        if hasattr(ev, "position"):
                            pp = ev.position()
                            if pp is not None:
                                return pp.toPoint()
                    except Exception:
                        pass
                    try:
                        if hasattr(ev, "pos"):
                            pp = ev.pos()
                            if pp is not None:
                                return pp
                    except Exception:
                        pass
                    return QtCore.QPoint(0, 0)

                def _pixel_x_to_value(self, x_pos: int) -> Optional[int]:
                    try:
                        opt = QtWidgets.QStyleOptionSlider()
                        self.initStyleOption(opt)
                        style = self.style()
                        groove = style.subControlRect(
                            QtWidgets.QStyle.CC_Slider,
                            opt,
                            QtWidgets.QStyle.SC_SliderGroove,
                            self,
                        )
                        span = int(
                            max(
                                1,
                                style.pixelMetric(
                                    QtWidgets.QStyle.PM_SliderSpaceAvailable,
                                    opt,
                                    self,
                                ),
                            )
                        )
                        slider_len = int(
                            max(
                                1,
                                style.pixelMetric(
                                    QtWidgets.QStyle.PM_SliderLength,
                                    opt,
                                    self,
                                ),
                            )
                        )
                        pos = int(round(float(x_pos) - float(groove.left()) - (float(slider_len) * 0.5)))
                        pos = max(0, min(span, pos))
                        val = int(
                            QtWidgets.QStyle.sliderValueFromPosition(
                                int(self.minimum()),
                                int(self.maximum()),
                                int(pos),
                                int(span),
                                bool(getattr(opt, "upsideDown", False)),
                            )
                        )
                        return max(int(self.minimum()), min(int(self.maximum()), int(val)))
                    except Exception:
                        return None

                def mousePressEvent(self, ev):
                    if ev.button() != QtCore.Qt.LeftButton:
                        return super().mousePressEvent(ev)
                    pt = self._event_pos(ev)
                    try:
                        opt = QtWidgets.QStyleOptionSlider()
                        self.initStyleOption(opt)
                        style = self.style()
                        handle = style.subControlRect(
                            QtWidgets.QStyle.CC_Slider,
                            opt,
                            QtWidgets.QStyle.SC_SliderHandle,
                            self,
                        )
                        if handle is not None and handle.contains(pt):
                            self._jump_drag_active = False
                            return super().mousePressEvent(ev)
                    except Exception:
                        pass
                    target = self._pixel_x_to_value(int(pt.x()))
                    if target is None:
                        self._jump_drag_active = False
                        return super().mousePressEvent(ev)
                    try:
                        self.setSliderPosition(int(target))
                    except Exception:
                        pass
                    try:
                        self.setValue(int(target))
                    except Exception:
                        pass
                    self._jump_drag_active = True
                    try:
                        self.setSliderDown(True)
                    except Exception:
                        pass
                    try:
                        ev.accept()
                    except Exception:
                        pass
                    return

                def mouseMoveEvent(self, ev):
                    if not bool(getattr(self, "_jump_drag_active", False)):
                        return super().mouseMoveEvent(ev)
                    try:
                        if not bool(ev.buttons() & QtCore.Qt.LeftButton):
                            self._jump_drag_active = False
                            try:
                                self.setSliderDown(False)
                            except Exception:
                                pass
                            return super().mouseMoveEvent(ev)
                    except Exception:
                        pass
                    pt = self._event_pos(ev)
                    target = self._pixel_x_to_value(int(pt.x()))
                    if target is not None:
                        try:
                            self.setSliderPosition(int(target))
                        except Exception:
                            pass
                        try:
                            self.setValue(int(target))
                        except Exception:
                            pass
                    try:
                        ev.accept()
                    except Exception:
                        pass
                    return

                def mouseReleaseEvent(self, ev):
                    if ev.button() != QtCore.Qt.LeftButton:
                        return super().mouseReleaseEvent(ev)
                    if not bool(getattr(self, "_jump_drag_active", False)):
                        return super().mouseReleaseEvent(ev)
                    pt = self._event_pos(ev)
                    target = self._pixel_x_to_value(int(pt.x()))
                    if target is not None:
                        try:
                            self.setSliderPosition(int(target))
                        except Exception:
                            pass
                        try:
                            self.setValue(int(target))
                        except Exception:
                            pass
                    self._jump_drag_active = False
                    try:
                        self.setSliderDown(False)
                    except Exception:
                        pass
                    try:
                        ev.accept()
                    except Exception:
                        pass
                    return

                def paintEvent(self, ev):
                    super().paintEvent(ev)
                    try:
                        base_opt = QtWidgets.QStyleOptionSlider()
                        self.initStyleOption(base_opt)
                        style = self.style()
                        groove = style.subControlRect(
                            QtWidgets.QStyle.CC_Slider,
                            base_opt,
                            QtWidgets.QStyle.SC_SliderGroove,
                            self,
                        )
                        if groove is None or not groove.isValid():
                            return
                        vmin = int(self.minimum())
                        vmax = int(self.maximum())
                        if vmax < vmin:
                            return
                        try:
                            start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                        except Exception:
                            start = 0
                        y_mid = int(groove.center().y())
                        p = QtGui.QPainter(self)
                        try:
                            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
                        except Exception:
                            pass
                        major_pen = QtGui.QPen(QtGui.QColor(226, 232, 240, 220), 1)
                        minor_pen = QtGui.QPen(QtGui.QColor(148, 163, 184, 170), 1)
                        for value in range(vmin, vmax + 1):
                            x = self._view._timeline_slider_value_to_x(int(value), slider=self)
                            if x is None:
                                continue
                            frame_val = int(start + value)
                            if (frame_val % 15) == 0:
                                p.setPen(major_pen)
                                y_top = int(y_mid - 7)
                                y_bot = int(y_mid + 7)
                            else:
                                p.setPen(minor_pen)
                                y_top = int(y_mid - 4)
                                y_bot = int(y_mid + 4)
                            p.drawLine(int(x), int(y_top), int(x), int(y_bot))
                        # Keep the custom keyframe handle icon above frame dashes.
                        handle_opt = QtWidgets.QStyleOptionSlider()
                        self.initStyleOption(handle_opt)
                        handle_opt.subControls = QtWidgets.QStyle.SC_SliderHandle
                        handle_opt.activeSubControls = QtWidgets.QStyle.SC_SliderHandle
                        style.drawComplexControl(
                            QtWidgets.QStyle.CC_Slider,
                            handle_opt,
                            p,
                            self,
                        )
                        p.end()
                    except Exception:
                        return

            frame_slider = _TimelineFrameSlider(self, timeline_area)
            frame_slider.setObjectName("GLTimelineFrameSlider")
            frame_slider.setRange(0, 7)
            frame_slider.setValue(0)
            frame_slider.setMinimumHeight(28)
            frame_slider.setTickPosition(QtWidgets.QSlider.NoTicks)
            frame_slider.valueChanged.connect(self._timeline_on_frame_slider_changed)
            timeline_area_layout.addWidget(frame_slider, 0)
            self._timeline_frame_slider = frame_slider

            class _TimelineCurveCanvas(QtWidgets.QWidget):
                def __init__(self, view, host):
                    super().__init__(host)
                    self._view = view
                    self._drag = None
                    self._selecting = False
                    self._select_origin = QtCore.QPointF()
                    self._selection_rect = QtCore.QRectF()
                    self.setObjectName("GLTimelineCurveCanvas")
                    self.setMouseTracking(True)
                    self.setAttribute(QtCore.Qt.WA_StyledBackground, True)

                @staticmethod
                def _event_pos(ev):
                    try:
                        if hasattr(ev, "position"):
                            return ev.position()
                    except Exception:
                        pass
                    return ev.pos()

                def _graph_rect(self):
                    left = 30
                    top = 8
                    right = 8
                    bottom = 8
                    return QtCore.QRect(
                        int(left),
                        int(top),
                        int(max(1, self.width() - left - right)),
                        int(max(1, self.height() - top - bottom)),
                    )

                def _value_to_y(self, value: float) -> float:
                    rect = self._graph_rect()
                    vmin = float(getattr(self._view, "_timeline_curve_min", -5.0))
                    vmax = float(getattr(self._view, "_timeline_curve_max", 5.0))
                    if vmax <= vmin:
                        return float(rect.center().y())
                    vv = max(vmin, min(vmax, float(value)))
                    t = (vmax - vv) / (vmax - vmin)
                    return float(rect.top()) + (t * float(max(1, rect.height())))

                def _y_to_value(self, y_pos: float) -> float:
                    rect = self._graph_rect()
                    vmin = float(getattr(self._view, "_timeline_curve_min", -5.0))
                    vmax = float(getattr(self._view, "_timeline_curve_max", 5.0))
                    if vmax <= vmin:
                        return 0.0
                    yy = max(float(rect.top()), min(float(rect.bottom()), float(y_pos)))
                    t = (yy - float(rect.top())) / float(max(1, rect.height()))
                    vv = vmax - (t * (vmax - vmin))
                    return float(max(vmin, min(vmax, vv)))

                def _axis_points(self):
                    out = {i: [] for i in range(6)}
                    slider = getattr(self._view, "_timeline_frame_slider", None)
                    if slider is None:
                        return out
                    try:
                        start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                    except Exception:
                        start = 0
                    try:
                        local_max = int(max(0, int(slider.maximum())))
                    except Exception:
                        local_max = 0
                    end = start + local_max
                    keys = getattr(self._view, "_timeline_keys", {}) or {}
                    try:
                        items = sorted(keys.items(), key=lambda kv: int(kv[0]))
                    except Exception:
                        items = list(keys.items())
                    for frame_raw, entry in items:
                        try:
                            frame = int(frame_raw)
                        except Exception:
                            continue
                        if frame < start or frame > end:
                            continue
                        local = frame - start
                        x = self._view._timeline_slider_to_tracks_x(local)
                        if x is None:
                            continue
                        for axis in self._view._timeline_axes_for_entry(entry):
                            if not bool(self._view._timeline_axis_is_visible(int(axis))):
                                continue
                            val = self._view._timeline_axis_value_for_entry(entry, axis)
                            if val is None:
                                continue
                            out[axis].append((int(frame), float(val), float(x), float(self._value_to_y(val))))
                    try:
                        visible_axes = sorted(
                            [
                                int(a)
                                for a in range(6)
                                if bool(self._view._timeline_axis_is_visible(int(a)))
                            ]
                        )
                        count_all = int(sum(len(v) for v in out.values()))
                        self._view._timeline_axis_log(
                            f"curve_axis_points visible_axes={visible_axes} points={count_all}",
                            throttle_key="curve_axis_points",
                            interval=1.0,
                        )
                    except Exception:
                        pass
                    for axis in out.keys():
                        out[axis].sort(key=lambda r: int(r[0]))
                    return out

                def _nearest_point(self, posf):
                    pts_by_axis = self._axis_points()
                    best = None
                    best_d2 = None
                    px = float(posf.x())
                    py = float(posf.y())
                    for axis, rows in pts_by_axis.items():
                        for frame, val, x, y in rows:
                            dx = float(x) - px
                            dy = float(y) - py
                            d2 = (dx * dx) + (dy * dy)
                            if d2 > 64.0:
                                continue
                            if best is None or best_d2 is None or d2 < best_d2:
                                best = (int(axis), int(frame), float(val))
                                best_d2 = d2
                    return best

                def _selected_set(self):
                    sel = getattr(self._view, "_timeline_curve_selected", None)
                    if isinstance(sel, set):
                        return set((int(a), int(f)) for (a, f) in sel)
                    return set()

                def _set_selected_set(self, items):
                    try:
                        self._view._timeline_curve_selected = {
                            (int(a), int(f)) for (a, f) in items
                        }
                    except Exception:
                        self._view._timeline_curve_selected = set()

                @staticmethod
                def _norm_rect(rf):
                    try:
                        return QtCore.QRectF(rf).normalized()
                    except Exception:
                        return QtCore.QRectF()

                def _keys_in_rect(self, rectf):
                    rr = self._norm_rect(rectf)
                    out = set()
                    pts_by_axis = self._axis_points()
                    for axis, rows in pts_by_axis.items():
                        for frame, _val, x, y in rows:
                            if rr.contains(QtCore.QPointF(float(x), float(y))):
                                out.add((int(axis), int(frame)))
                    return out

                def paintEvent(self, ev):
                    _ = ev
                    p = QtGui.QPainter(self)
                    try:
                        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                    except Exception:
                        pass
                    rect = self._graph_rect()
                    p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 0))
                    p.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
                    p.drawLine(rect.left(), rect.top(), rect.left(), rect.bottom())
                    p.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

                    vmin = int(round(float(getattr(self._view, "_timeline_curve_min", -5.0))))
                    vmax = int(round(float(getattr(self._view, "_timeline_curve_max", 5.0))))
                    if vmax < vmin:
                        vmin, vmax = vmax, vmin
                    for vv in range(vmax, vmin - 1, -1):
                        y = int(round(self._value_to_y(float(vv))))
                        line_col = QtGui.QColor(148, 163, 184, 70)
                        if vv == 0:
                            line_col = QtGui.QColor(148, 163, 184, 115)
                        p.setPen(QtGui.QPen(line_col, 1))
                        p.drawLine(rect.left(), y, rect.right(), y)
                        p.setPen(QtGui.QPen(QtGui.QColor("#cbd5e1"), 1))
                        p.drawText(2, y - 2, f"{vv}")

                    pts_by_axis = self._axis_points()
                    selected = self._selected_set()
                    for axis in range(6):
                        rows = pts_by_axis.get(axis, [])
                        if not rows:
                            continue
                        col = self._view._timeline_axis_color(axis)
                        path = QtGui.QPainterPath()
                        path.moveTo(float(rows[0][2]), float(rows[0][3]))
                        if len(rows) > 1:
                            qpts = [QtCore.QPointF(float(r[2]), float(r[3])) for r in rows]
                            for i in range(len(qpts) - 1):
                                p0 = qpts[i - 1] if i > 0 else qpts[i]
                                p1 = qpts[i]
                                p2 = qpts[i + 1]
                                p3 = qpts[i + 2] if (i + 2) < len(qpts) else qpts[i + 1]
                                c1 = QtCore.QPointF(
                                    p1.x() + ((p2.x() - p0.x()) / 6.0),
                                    p1.y() + ((p2.y() - p0.y()) / 6.0),
                                )
                                c2 = QtCore.QPointF(
                                    p2.x() - ((p3.x() - p1.x()) / 6.0),
                                    p2.y() - ((p3.y() - p1.y()) / 6.0),
                                )
                                path.cubicTo(c1, c2, p2)
                        p.setBrush(QtCore.Qt.NoBrush)
                        p.setPen(QtGui.QPen(col, 2))
                        p.drawPath(path)
                        for frame, _val, x, y in rows:
                            key = (int(axis), int(frame))
                            if key in selected:
                                p.setPen(QtGui.QPen(QtGui.QColor("#f59e0b"), 1.2))
                                p.setBrush(QtGui.QBrush(QtGui.QColor("#fde047")))
                                p.drawEllipse(QtCore.QPointF(float(x), float(y)), 5.0, 5.0)
                            else:
                                p.setPen(QtGui.QPen(QtGui.QColor(15, 23, 42, 210), 1))
                                p.setBrush(QtGui.QBrush(col))
                                p.drawEllipse(QtCore.QPointF(float(x), float(y)), 4.0, 4.0)
                    if bool(self._selecting):
                        rr = self._norm_rect(self._selection_rect)
                        p.setPen(QtGui.QPen(QtGui.QColor("#fde047"), 1, QtCore.Qt.DashLine))
                        p.setBrush(QtGui.QBrush(QtGui.QColor(253, 224, 71, 35)))
                        p.drawRect(rr)
                    p.end()

                def mousePressEvent(self, ev):
                    if ev.button() != QtCore.Qt.LeftButton:
                        return super().mousePressEvent(ev)
                    posf = self._event_pos(ev)
                    hit = self._nearest_point(posf)
                    if hit is None:
                        self._drag = None
                        self._selecting = True
                        self._select_origin = QtCore.QPointF(float(posf.x()), float(posf.y()))
                        self._selection_rect = QtCore.QRectF(self._select_origin, self._select_origin)
                        self._set_selected_set(set())
                        self.update()
                        ev.accept()
                        return
                    axis, frame, _val = hit
                    self._selecting = False
                    hit_key = (int(axis), int(frame))
                    selected = self._selected_set()
                    if hit_key in selected and len(selected) > 1:
                        drag_sel = set(selected)
                    else:
                        drag_sel = {hit_key}
                    self._drag = {
                        "axis": int(axis),
                        "frame": int(frame),
                        "value": float(_val),
                        "multi": bool(len(drag_sel) > 1),
                        "dirty": False,
                    }
                    self._set_selected_set(drag_sel)
                    self.update()
                    ev.accept()

                def mouseMoveEvent(self, ev):
                    if bool(self._selecting):
                        posf = self._event_pos(ev)
                        self._selection_rect = QtCore.QRectF(self._select_origin, QtCore.QPointF(float(posf.x()), float(posf.y())))
                        self._set_selected_set(self._keys_in_rect(self._selection_rect))
                        self.update()
                        ev.accept()
                        return
                    if not isinstance(self._drag, dict):
                        return super().mouseMoveEvent(ev)
                    posf = self._event_pos(ev)
                    local = self._view._timeline_tracks_x_to_slider_value(int(round(float(posf.x()))))
                    if local is None:
                        return
                    try:
                        start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                    except Exception:
                        start = 0
                    target_frame = max(0, start + int(local))
                    target_value = self._y_to_value(float(posf.y()))
                    cur_frame = int(self._drag.get("frame", target_frame))
                    if bool(self._drag.get("multi", False)):
                        cur_value = float(self._drag.get("value", target_value))
                        delta_frame = int(target_frame) - int(cur_frame)
                        delta_value = float(target_value) - float(cur_value)
                        if delta_frame != 0 or abs(delta_value) > 1.0e-9:
                            moved_f, moved_v = self._view._timeline_drag_selected_curve_keys(
                                int(delta_frame),
                                float(delta_value),
                                commit=False,
                            )
                            self._drag["frame"] = int(cur_frame + int(moved_f))
                            self._drag["value"] = float(cur_value + float(moved_v))
                            if int(moved_f) != 0 or abs(float(moved_v)) > 1.0e-9:
                                self._drag["dirty"] = True
                    else:
                        new_frame = self._view._timeline_drag_axis_key(
                            int(self._drag.get("axis", 0)),
                            int(cur_frame),
                            int(target_frame),
                            float(target_value),
                            commit=False,
                        )
                        self._drag["frame"] = int(new_frame)
                        self._drag["value"] = float(target_value)
                    self.update()
                    ev.accept()

                def mouseReleaseEvent(self, ev):
                    if ev.button() != QtCore.Qt.LeftButton:
                        return super().mouseReleaseEvent(ev)
                    if bool(self._selecting):
                        self._selecting = False
                        rr = self._norm_rect(self._selection_rect)
                        if rr.width() <= 2.0 and rr.height() <= 2.0:
                            self._set_selected_set(set())
                        else:
                            self._set_selected_set(self._keys_in_rect(rr))
                        self._selection_rect = QtCore.QRectF()
                        self.update()
                        ev.accept()
                        return
                    if not isinstance(self._drag, dict):
                        return super().mouseReleaseEvent(ev)
                    posf = self._event_pos(ev)
                    local = self._view._timeline_tracks_x_to_slider_value(int(round(float(posf.x()))))
                    if local is not None:
                        try:
                            start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                        except Exception:
                            start = 0
                        target_frame = max(0, start + int(local))
                        target_value = self._y_to_value(float(posf.y()))
                        cur_frame = int(self._drag.get("frame", target_frame))
                        if bool(self._drag.get("multi", False)):
                            committed = False
                            cur_value = float(self._drag.get("value", target_value))
                            delta_frame = int(target_frame) - int(cur_frame)
                            delta_value = float(target_value) - float(cur_value)
                            if delta_frame != 0 or abs(delta_value) > 1.0e-9:
                                moved_f, moved_v = self._view._timeline_drag_selected_curve_keys(
                                    int(delta_frame),
                                    float(delta_value),
                                    commit=True,
                                )
                                self._drag["frame"] = int(cur_frame + int(moved_f))
                                self._drag["value"] = float(cur_value + float(moved_v))
                                if int(moved_f) != 0 or abs(float(moved_v)) > 1.0e-9:
                                    committed = True
                                    self._drag["dirty"] = False
                            if bool(self._drag.get("dirty", False)) and not bool(committed):
                                try:
                                    self._view._timeline_save_to_disk()
                                    self._drag["dirty"] = False
                                except Exception:
                                    pass
                        else:
                            new_frame = self._view._timeline_drag_axis_key(
                                int(self._drag.get("axis", 0)),
                                int(cur_frame),
                                int(target_frame),
                                float(target_value),
                                commit=True,
                            )
                            self._drag["frame"] = int(new_frame)
                            self._set_selected_set({(int(self._drag.get("axis", 0)), int(new_frame))})
                    self._drag = None
                    self.update()
                    ev.accept()

            class _TimelineRowsHost(QtWidgets.QWidget):
                def __init__(self, view, host):
                    super().__init__(host)
                    self._view = view
                    self._drag = None
                    self._selecting = False
                    self._select_origin = QtCore.QPointF()
                    self._selection_rect = QtCore.QRectF()
                    self.setMouseTracking(True)

                @staticmethod
                def _event_pos(ev):
                    try:
                        if hasattr(ev, "position"):
                            return ev.position()
                    except Exception:
                        pass
                    return ev.pos()

                @staticmethod
                def _norm_rect(rf):
                    try:
                        return QtCore.QRectF(rf).normalized()
                    except Exception:
                        return QtCore.QRectF()

                def paintEvent(self, ev):
                    super().paintEvent(ev)
                    _ = ev
                    if not bool(self._selecting):
                        return
                    rr = self._norm_rect(self._selection_rect)
                    if rr.isNull():
                        return
                    p = QtGui.QPainter(self)
                    try:
                        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                    except Exception:
                        pass
                    p.setPen(QtGui.QPen(QtGui.QColor("#fde047"), 1, QtCore.Qt.DashLine))
                    p.setBrush(QtGui.QBrush(QtGui.QColor(253, 224, 71, 35)))
                    p.drawRect(rr)
                    p.end()

                def mousePressEvent(self, ev):
                    if ev.button() != QtCore.Qt.LeftButton:
                        return super().mousePressEvent(ev)
                    if bool(getattr(self._view, "_timeline_curves_mode", False)):
                        return super().mousePressEvent(ev)
                    posf = self._event_pos(ev)
                    hit = self._view._timeline_hit_row_key(int(round(float(posf.x()))), int(round(float(posf.y()))))
                    if hit is None:
                        self._drag = None
                        self._selecting = True
                        self._select_origin = QtCore.QPointF(float(posf.x()), float(posf.y()))
                        self._selection_rect = QtCore.QRectF(self._select_origin, self._select_origin)
                        self._view._timeline_curve_selected = set()
                        self._view._timeline_update_key_markers()
                        self.update()
                        ev.accept()
                        return
                    axis, frame, value = hit
                    self._selecting = False
                    hit_key = (int(axis), int(frame))
                    try:
                        selected = {
                            (int(a), int(f))
                            for (a, f) in (getattr(self._view, "_timeline_curve_selected", set()) or set())
                        }
                    except Exception:
                        selected = set()
                    if hit_key in selected and len(selected) > 1:
                        drag_sel = set(selected)
                    else:
                        drag_sel = {hit_key}
                    self._drag = {
                        "axis": int(axis),
                        "frame": int(frame),
                        "value": float(value),
                        "multi": bool(len(drag_sel) > 1),
                        "dirty": False,
                    }
                    self._view._timeline_curve_selected = {
                        (int(a), int(f))
                        for (a, f) in drag_sel
                    }
                    self._view._timeline_update_key_markers()
                    self.update()
                    ev.accept()

                def mouseMoveEvent(self, ev):
                    if bool(getattr(self._view, "_timeline_curves_mode", False)):
                        return super().mouseMoveEvent(ev)
                    posf = self._event_pos(ev)
                    if bool(self._selecting):
                        self._selection_rect = QtCore.QRectF(
                            self._select_origin,
                            QtCore.QPointF(float(posf.x()), float(posf.y())),
                        )
                        self._view._timeline_curve_selected = self._view._timeline_keys_in_rows_rect(self._selection_rect)
                        self._view._timeline_update_key_markers()
                        self.update()
                        ev.accept()
                        return
                    if not isinstance(self._drag, dict):
                        return super().mouseMoveEvent(ev)
                    local = self._view._timeline_tracks_x_to_slider_value(int(round(float(posf.x()))))
                    if local is None:
                        return
                    try:
                        start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                    except Exception:
                        start = 0
                    axis = int(self._drag.get("axis", 0))
                    cur_frame = int(self._drag.get("frame", 0))
                    target_frame = max(0, start + int(local))
                    target_value = float(self._drag.get("value", 0.0))
                    if bool(self._drag.get("multi", False)):
                        delta = int(target_frame) - int(cur_frame)
                        if delta != 0:
                            moved = self._view._timeline_drag_selected_keys(int(delta), commit=False)
                            self._drag["frame"] = int(cur_frame + int(moved))
                            if int(moved) != 0:
                                self._drag["dirty"] = True
                    else:
                        new_frame = self._view._timeline_drag_axis_key(
                            int(axis),
                            int(cur_frame),
                            int(target_frame),
                            float(target_value),
                            commit=False,
                        )
                        self._drag["frame"] = int(new_frame)
                        self._view._timeline_curve_selected = {(int(axis), int(new_frame))}
                    self._view._timeline_update_key_markers()
                    self.update()
                    ev.accept()

                def mouseReleaseEvent(self, ev):
                    if ev.button() != QtCore.Qt.LeftButton:
                        return super().mouseReleaseEvent(ev)
                    if bool(getattr(self._view, "_timeline_curves_mode", False)):
                        return super().mouseReleaseEvent(ev)
                    if bool(self._selecting):
                        self._selecting = False
                        rr = self._norm_rect(self._selection_rect)
                        if rr.width() <= 2.0 and rr.height() <= 2.0:
                            self._view._timeline_curve_selected = set()
                        else:
                            self._view._timeline_curve_selected = self._view._timeline_keys_in_rows_rect(rr)
                        self._selection_rect = QtCore.QRectF()
                        self._view._timeline_update_key_markers()
                        self.update()
                        ev.accept()
                        return
                    if not isinstance(self._drag, dict):
                        return super().mouseReleaseEvent(ev)
                    posf = self._event_pos(ev)
                    local = self._view._timeline_tracks_x_to_slider_value(int(round(float(posf.x()))))
                    axis = int(self._drag.get("axis", 0))
                    cur_frame = int(self._drag.get("frame", 0))
                    if local is not None:
                        try:
                            start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                        except Exception:
                            start = 0
                        target_frame = max(0, start + int(local))
                        target_value = float(self._drag.get("value", 0.0))
                        if bool(self._drag.get("multi", False)):
                            committed = False
                            delta = int(target_frame) - int(cur_frame)
                            if delta != 0:
                                moved = self._view._timeline_drag_selected_keys(int(delta), commit=True)
                                self._drag["frame"] = int(cur_frame + int(moved))
                                if int(moved) != 0:
                                    committed = True
                                    self._drag["dirty"] = False
                            if bool(self._drag.get("dirty", False)) and not bool(committed):
                                try:
                                    self._view._timeline_save_to_disk()
                                    self._drag["dirty"] = False
                                except Exception:
                                    pass
                        else:
                            new_frame = self._view._timeline_drag_axis_key(
                                int(axis),
                                int(cur_frame),
                                int(target_frame),
                                float(target_value),
                                commit=True,
                            )
                            self._drag["frame"] = int(new_frame)
                            self._view._timeline_curve_selected = {(int(axis), int(new_frame))}
                    self._drag = None
                    self._view._timeline_update_key_markers()
                    self.update()
                    ev.accept()

            tracks_frame = QtWidgets.QFrame(timeline_area)
            tracks_frame.setObjectName("GLTimelineTracks")
            tracks_frame.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.MinimumExpanding)
            tracks_stack = QtWidgets.QStackedLayout(tracks_frame)
            tracks_stack.setContentsMargins(0, 0, 0, 0)
            tracks_stack.setSpacing(0)
            self._timeline_tracks_stack = tracks_stack

            rows_host = _TimelineRowsHost(self, tracks_frame)
            self._timeline_rows_host = rows_host
            tracks_layout = QtWidgets.QVBoxLayout(rows_host)
            tracks_layout.setContentsMargins(0, 0, 0, 0)
            tracks_layout.setSpacing(4)
            track_rows: List[QtWidgets.QFrame] = []
            for _ in channels:
                row_frame = QtWidgets.QFrame(rows_host)
                row_frame.setObjectName("GLTimelineTrackRow")
                row_frame.setFixedHeight(22)
                row_frame.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
                row_layout = QtWidgets.QVBoxLayout(row_frame)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(0)
                row_layout.addStretch(1)
                row_line = QtWidgets.QFrame(row_frame)
                row_line.setObjectName("GLTimelineTrackLine")
                row_line.setFixedHeight(1)
                row_line.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
                row_layout.addWidget(row_line, 0)
                row_layout.addStretch(1)
                tracks_layout.addWidget(row_frame, 0)
                track_rows.append(row_frame)
            tracks_stack.addWidget(rows_host)

            curves_canvas = _TimelineCurveCanvas(self, tracks_frame)
            self._timeline_curves_canvas = curves_canvas
            tracks_stack.addWidget(curves_canvas)
            timeline_area_layout.addWidget(tracks_frame, 1)

            playhead = QtWidgets.QFrame(tracks_frame)
            playhead.setObjectName("GLTimelinePlayhead")
            playhead.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            playhead.hide()
            self._timeline_tracks_frame = tracks_frame
            self._timeline_playhead = playhead
            self._timeline_track_rows = track_rows
            self._timeline_key_markers = []

            class _TimelineTracksFilter(QtCore.QObject):
                def __init__(self, view, host):
                    super().__init__(host)
                    self._view = view

                def eventFilter(self, obj, ev):
                    if ev.type() == QtCore.QEvent.MouseButtonPress:
                        try:
                            self._view.setFocus(QtCore.Qt.MouseFocusReason)
                        except Exception:
                            try:
                                self._view.setFocus()
                            except Exception:
                                pass
                    if ev.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
                        try:
                            QtCore.QTimer.singleShot(0, self._view._timeline_sync_row_alignment)
                        except Exception:
                            pass
                        try:
                            QtCore.QTimer.singleShot(0, self._view._timeline_update_key_markers)
                        except Exception:
                            pass
                        try:
                            QtCore.QTimer.singleShot(0, self._view._timeline_update_playhead)
                        except Exception:
                            pass
                        try:
                            QtCore.QTimer.singleShot(0, self._view._timeline_update_tick_labels)
                        except Exception:
                            pass
                    return False

            tracks_frame._timeline_tracks_filter = _TimelineTracksFilter(self, tracks_frame)
            tracks_frame.installEventFilter(tracks_frame._timeline_tracks_filter)
            frame_slider.installEventFilter(tracks_frame._timeline_tracks_filter)
            timeline_area.installEventFilter(tracks_frame._timeline_tracks_filter)
            rows_host.installEventFilter(tracks_frame._timeline_tracks_filter)
            curves_canvas.installEventFilter(tracks_frame._timeline_tracks_filter)

            tracks_grid.addWidget(timeline_area, 0, 2, len(channels) + 1, 1)
            tracks_grid.setColumnStretch(2, 1)
            root.addLayout(tracks_grid, 1)

            scrollbar = QtWidgets.QScrollBar(QtCore.Qt.Horizontal, panel)
            scrollbar.setRange(0, 0)
            scrollbar.setSingleStep(1)
            scrollbar.setPageStep(24)
            scrollbar.valueChanged.connect(self._timeline_on_scroll_changed)
            root.addWidget(scrollbar, 0)
            self._timeline_scrollbar = scrollbar

            self._timeline_sync_row_alignment()
            self._timeline_sync_range_controls(keep_current_visible=True, refresh_key_markers=True)
            self._timeline_set_curves_mode(bool(getattr(self, "_timeline_curves_mode", False)))

            panel.hide()
            self._timeline_panel = panel
            self._layout_timeline_panel()
            self._timeline_refresh_coord_labels()
            try:
                QtCore.QTimer.singleShot(0, self._timeline_sync_row_alignment)
            except Exception:
                pass
        except Exception:
            self._timeline_panel = None
            self._timeline_tracks_frame = None
            self._timeline_playhead = None
            self._timeline_tracks_stack = None
            self._timeline_rows_host = None
            self._timeline_curves_canvas = None
            self._timeline_left_header_spacer = None
            self._timeline_area_widget = None
            self._timeline_axis_labels = []
            self._timeline_track_rows = []
            self._timeline_key_markers = []
            self._timeline_scrollbar = None
            self._timeline_play_btn = None
            self._timeline_curves_btn = None
            self._timeline_frame_slider = None
            self._timeline_frame_spin = None
            self._timeline_key_count_label = None
            self._timeline_tick_labels = []
            self._timeline_ticks_frame = None

    def timeline_visible(self) -> bool:
        return bool(getattr(self, "_timeline_enabled", False))

    def set_timeline_visible(self, visible: bool) -> None:
        want = bool(visible)
        self._build_timeline_panel()
        if want == bool(getattr(self, "_timeline_enabled", False)):
            panel = getattr(self, "_timeline_panel", None)
            if panel is not None:
                panel.setVisible(want)
                self._layout_timeline_panel()
            if not want:
                try:
                    self._timeline_ui_timer.stop()
                except Exception:
                    pass
                try:
                    self._timeline_play_timer.stop()
                except Exception:
                    pass
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
                    self._update_timeline_play_button()
            return
        self._timeline_enabled = want
        panel = getattr(self, "_timeline_panel", None)
        if panel is not None:
            panel.setVisible(want)
            self._layout_timeline_panel()
        if want:
            try:
                self.set_timeline_scene_context()
            except Exception:
                pass
            try:
                self._timeline_refresh_coord_labels()
            except Exception:
                pass
            try:
                self._timeline_ui_timer.start()
            except Exception:
                pass
        else:
            try:
                self._timeline_ui_timer.stop()
            except Exception:
                pass
            try:
                self._timeline_play_timer.stop()
            except Exception:
                pass
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
                self._update_timeline_play_button()
        try:
            self.update()
        except Exception:
            pass


