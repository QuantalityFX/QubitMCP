from __future__ import annotations

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

