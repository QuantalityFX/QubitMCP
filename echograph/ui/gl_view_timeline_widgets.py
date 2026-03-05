from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import List, Optional

from echograph.qt_compat import QtCore, QtGui, QtWidgets

try:
    from PySide6 import QtMultimedia
except Exception:
    try:
        from PySide2 import QtMultimedia  # type: ignore
    except Exception:
        QtMultimedia = None  # type: ignore


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


def _audio_waveform_from_wav(path: Path, bins: int = 1024) -> tuple[list[float], int]:
    p = Path(path)
    if not p.is_file():
        return ([], 0)
    try:
        wf = wave.open(str(p), "rb")
    except Exception:
        return ([], 0)
    with wf:
        try:
            rate = int(max(1, wf.getframerate()))
            frame_count = int(max(0, wf.getnframes()))
            sampwidth = int(max(1, wf.getsampwidth()))
        except Exception:
            return ([], 0)
        if frame_count <= 0:
            return ([], 0)
        duration_ms = int(round((float(frame_count) / float(rate)) * 1000.0))
        target_bins = int(max(64, min(4096, int(bins))))
        frames_per_bin = int(max(1, math.ceil(float(frame_count) / float(target_bins))))
        peaks: list[float] = []
        for _ in range(target_bins):
            try:
                raw = wf.readframes(frames_per_bin)
            except Exception:
                raw = b""
            if not raw:
                break
            peak = 0.0
            if sampwidth == 1:
                for bb in raw:
                    vv = abs(float(int(bb) - 128) / 128.0)
                    if vv > peak:
                        peak = vv
            elif sampwidth == 2:
                usable = len(raw) - (len(raw) % 2)
                if usable > 0:
                    for (sv,) in struct.iter_unpack("<h", raw[:usable]):
                        vv = abs(float(sv) / 32768.0)
                        if vv > peak:
                            peak = vv
            elif sampwidth == 3:
                usable = len(raw) - (len(raw) % 3)
                data = raw[:usable]
                for i in range(0, len(data), 3):
                    iv = int(data[i]) | (int(data[i + 1]) << 8) | (int(data[i + 2]) << 16)
                    if iv & 0x800000:
                        iv -= 0x1000000
                    vv = abs(float(iv) / 8388608.0)
                    if vv > peak:
                        peak = vv
            else:
                usable = len(raw) - (len(raw) % 4)
                if usable > 0:
                    for (sv,) in struct.iter_unpack("<i", raw[:usable]):
                        vv = abs(float(sv) / 2147483648.0)
                        if vv > peak:
                            peak = vv
            peaks.append(max(0.0, min(1.0, float(peak))))
        if not peaks:
            peaks = [0.0]
        return (peaks, int(max(0, duration_ms)))


def _audio_waveform_from_file_bytes(path: Path, bins: int = 1024) -> list[float]:
    p = Path(path)
    if not p.is_file():
        return []
    try:
        file_size = int(max(0, p.stat().st_size))
    except Exception:
        return []
    if file_size <= 0:
        return []
    target_bins = int(max(64, min(4096, int(bins))))
    chunk = int(max(1, math.ceil(float(file_size) / float(target_bins))))
    peaks: list[float] = []
    try:
        with p.open("rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                peak = 0.0
                for bb in block:
                    vv = abs(float(int(bb) - 128) / 128.0)
                    if vv > peak:
                        peak = vv
                peaks.append(max(0.0, min(1.0, peak)))
    except Exception:
        return []
    if not peaks:
        return [0.0]
    return peaks


class _TimelineAudioWaveformWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples: list[float] = []
        self._playhead = 0.0
        self.setMinimumHeight(48)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)

    def setWaveform(self, samples) -> None:
        out: list[float] = []
        try:
            for val in (samples or []):
                out.append(max(0.0, min(1.0, float(val))))
        except Exception:
            out = []
        self._samples = out
        self.update()

    def setPlayhead(self, norm: float) -> None:
        try:
            nv = max(0.0, min(1.0, float(norm)))
        except Exception:
            nv = 0.0
        if abs(nv - float(self._playhead)) < 1.0e-4:
            return
        self._playhead = nv
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        outer = self.rect().adjusted(1, 1, -1, -1)
        if outer.width() <= 2 or outer.height() <= 2:
            return
        draw = outer.adjusted(2, 3, -2, -3)
        if draw.width() <= 2 or draw.height() <= 2:
            draw = outer
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        except Exception:
            pass
        p.fillRect(outer, QtGui.QColor(15, 23, 42, 180))
        p.setPen(QtGui.QPen(QtGui.QColor(51, 65, 85, 230), 1))
        p.drawRect(outer)
        samples = list(getattr(self, "_samples", []) or [])
        if not samples:
            p.setPen(QtGui.QPen(QtGui.QColor(148, 163, 184, 220), 1))
            p.drawText(draw, int(QtCore.Qt.AlignCenter), "No audio loaded")
            p.end()
            return
        mid_y = int(draw.center().y())
        amp_h = int(max(3, (draw.height() // 2) - 2))
        wave_pen = QtGui.QPen(QtGui.QColor(186, 203, 222, 220), 1)
        p.setPen(wave_pen)
        n = int(max(1, len(samples)))
        if n == 1:
            x = int(draw.left() + (draw.width() // 2))
            hh = int(max(1, min(float(amp_h), float(samples[0]) * float(amp_h))))
            p.drawLine(x, int(mid_y - hh), x, int(mid_y + hh))
        else:
            span = float(max(1, draw.width() - 1))
            for i, amp in enumerate(samples):
                x = int(round(float(draw.left()) + (float(i) / float(n - 1)) * span))
                hh = int(max(1, min(float(amp_h), float(amp) * float(amp_h))))
                p.drawLine(x, int(mid_y - hh), x, int(mid_y + hh))
        playhead_norm = max(0.0, min(1.0, float(getattr(self, "_playhead", 0.0))))
        play_x = int(round(float(draw.left()) + (playhead_norm * float(max(1, draw.width() - 1)))))
        p.setPen(QtGui.QPen(QtGui.QColor(248, 250, 252, 245), 1))
        p.drawLine(play_x, int(draw.top()), play_x, int(draw.bottom()))
        p.end()


class GraphGLTimelineWidgetsMixin:
    def _bottom_overlay_height(self) -> float:
        h = 0.0
        controls = getattr(self, "_controls", None)
        if controls is not None and controls.isVisible():
            h += float(getattr(self, "_controls_h", 0) or 0)
        panel = getattr(self, "_timeline_panel", None)
        if panel is not None and panel.isVisible():
            h += float(getattr(self, "_timeline_h", 0) or 0)
        audio_panel = getattr(self, "_timeline_audio_panel", None)
        if audio_panel is not None and audio_panel.isVisible():
            h += float(getattr(self, "_timeline_audio_h", 0) or 0)
        return h

    def _layout_timeline_panel(self) -> None:
        controls_h = 0
        controls = getattr(self, "_controls", None)
        if controls is not None and controls.isVisible():
            try:
                controls_h = int(getattr(self, "_controls_h", 44))
            except Exception:
                controls_h = 44
        view_w = max(1, int(self.width()))
        y_cursor = max(0, int(self.height()) - int(controls_h))

        panel = getattr(self, "_timeline_panel", None)
        if panel is not None and panel.isVisible():
            pref_h = 0
            try:
                pref_h = int(panel.sizeHint().height())
            except Exception:
                pref_h = 0
            h = int(max(88, int(getattr(self, "_timeline_h", 72) or 72), pref_h))
            self._timeline_h = h
            y = max(0, int(y_cursor) - int(h))
            panel.setGeometry(0, y, view_w, h)
            panel.raise_()
            y_cursor = y
            self._timeline_sync_row_alignment()
            self._timeline_update_key_markers()
            self._timeline_update_playhead()

        audio_panel = getattr(self, "_timeline_audio_panel", None)
        if audio_panel is not None and audio_panel.isVisible():
            pref_ah = 0
            try:
                pref_ah = int(audio_panel.sizeHint().height())
            except Exception:
                pref_ah = 0
            ah = int(max(64, int(getattr(self, "_timeline_audio_h", 88) or 88), pref_ah))
            self._timeline_audio_h = ah
            ay = max(0, int(y_cursor) - int(ah))
            audio_panel.setGeometry(0, ay, view_w, ah)
            audio_panel.raise_()

    def _build_timeline_panel(self) -> None:
        existing = getattr(self, "_timeline_panel", None)
        if existing is not None:
            has_play = isinstance(getattr(self, "_timeline_play_btn", None), QtWidgets.QPushButton)
            has_curves_btn = isinstance(getattr(self, "_timeline_curves_btn", None), QtWidgets.QPushButton)
            has_material_btn = isinstance(getattr(self, "_timeline_material_live_btn", None), QtWidgets.QPushButton)
            has_handle_straight_btn = isinstance(getattr(self, "_timeline_handle_straight_btn", None), QtWidgets.QPushButton)
            has_handle_tied_btn = isinstance(getattr(self, "_timeline_handle_tied_btn", None), QtWidgets.QPushButton)
            has_handle_untied_btn = isinstance(getattr(self, "_timeline_handle_untied_btn", None), QtWidgets.QPushButton)
            has_mark_in_btn = isinstance(getattr(self, "_timeline_mark_in_btn", None), QtWidgets.QPushButton)
            has_mark_out_btn = isinstance(getattr(self, "_timeline_mark_out_btn", None), QtWidgets.QPushButton)
            has_loop_btn = isinstance(getattr(self, "_timeline_loop_btn", None), QtWidgets.QPushButton)
            has_seed_spin = isinstance(getattr(self, "_timeline_texture_seed_spin", None), QtWidgets.QSpinBox)
            has_scroll = getattr(self, "_timeline_scrollbar", None) is not None
            has_spacer = getattr(self, "_timeline_left_header_spacer", None) is not None
            has_rows = bool(getattr(self, "_timeline_track_rows", []))
            has_stack = getattr(self, "_timeline_tracks_stack", None) is not None
            has_canvas = getattr(self, "_timeline_curves_canvas", None) is not None
            has_target_label = getattr(self, "_timeline_target_label", None) is not None
            has_axis_labels = (
                isinstance(getattr(self, "_timeline_axis_labels", None), list)
                and len(getattr(self, "_timeline_axis_labels", [])) == 6
                and all(
                    isinstance(lb, QtWidgets.QPushButton)
                    for lb in (getattr(self, "_timeline_axis_labels", []) or [])
                    if lb is not None
                )
            )
            if (
                has_play
                and has_curves_btn
                and has_material_btn
                and has_handle_straight_btn
                and has_handle_tied_btn
                and has_handle_untied_btn
                and has_mark_in_btn
                and has_mark_out_btn
                and has_loop_btn
                and has_seed_spin
                and has_scroll
                and has_spacer
                and has_rows
                and has_stack
                and has_canvas
                and has_target_label
                and has_axis_labels
            ):
                self._timeline_update_axis_label_styles()
                try:
                    self._update_timeline_material_live_button()
                except Exception:
                    pass
                try:
                    self._timeline_update_handle_mode_buttons()
                except Exception:
                    pass
                try:
                    self._update_timeline_loop_button()
                except Exception:
                    pass
                try:
                    self._timeline_update_range_button_tooltips()
                except Exception:
                    pass
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
            self._timeline_mark_in_btn = None
            self._timeline_mark_out_btn = None
            self._timeline_loop_btn = None
            self._timeline_curves_btn = None
            self._timeline_material_live_btn = None
            self._timeline_handle_straight_btn = None
            self._timeline_handle_tied_btn = None
            self._timeline_handle_untied_btn = None
            self._timeline_frame_slider = None
            self._timeline_frame_spin = None
            self._timeline_texture_seed_spin = None
            self._timeline_key_count_label = None
            self._timeline_tick_labels = []
            self._timeline_target_label = None
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
                    "#GLTimelinePanel QPushButton#GLTimelineLoopButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineLoopButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineLoopButton:checked{background:transparent;border:0px;}",
                    "#GLTimelinePanel QFrame#GLTimelineTracks{background:rgba(15,18,22,120);border:1px solid #334155;border-radius:4px;}",
                    "#GLTimelinePanel QFrame#GLTimelineTrackRow{background:rgba(15,18,22,34);border-radius:3px;}",
                    "#GLTimelinePanel QFrame#GLTimelineTrackLine{background:rgba(148,163,184,80);border:0px;}",
                    "#GLTimelinePanel QFrame#GLTimelineKeyDot{background:#ef4444;border:1px solid #991b1b;border-radius:4px;}",
                    "#GLTimelinePanel QFrame#GLTimelinePlayhead{background:#ffffff;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineCurvesButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineCurvesButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineCurvesButton:checked{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineMaterialLiveButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineMaterialLiveButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineMaterialLiveButton:checked{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleStraightButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleStraightButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleStraightButton:checked{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleTiedButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleTiedButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleTiedButton:checked{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleUntiedButton{padding:0px;background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleUntiedButton:hover{background:transparent;border:0px;}",
                    "#GLTimelinePanel QPushButton#GLTimelineHandleUntiedButton:checked{background:transparent;border:0px;}",
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

            mark_in_btn = QtWidgets.QPushButton("[", panel)
            mark_in_btn.setObjectName("GLTimelineMarkInButton")
            mark_in_btn.setFixedSize(24, 24)
            mark_in_btn.setToolTip("Set Start Frame ([)")
            mark_in_btn.clicked.connect(self._timeline_on_mark_in_clicked)
            header.addWidget(mark_in_btn, 0)
            self._timeline_mark_in_btn = mark_in_btn

            mark_out_btn = QtWidgets.QPushButton("]", panel)
            mark_out_btn.setObjectName("GLTimelineMarkOutButton")
            mark_out_btn.setFixedSize(24, 24)
            mark_out_btn.setToolTip("Set End Frame (])")
            mark_out_btn.clicked.connect(self._timeline_on_mark_out_clicked)
            header.addWidget(mark_out_btn, 0)
            self._timeline_mark_out_btn = mark_out_btn

            loop_btn = QtWidgets.QPushButton(panel)
            loop_btn.setObjectName("GLTimelineLoopButton")
            loop_btn.setCheckable(True)
            loop_btn.setFixedSize(26, 26)
            loop_btn.setFlat(True)
            loop_btn.toggled.connect(self._timeline_on_loop_toggled)
            header.addWidget(loop_btn, 0)
            self._timeline_loop_btn = loop_btn
            self._timeline_set_loop_enabled(bool(getattr(self, "_timeline_loop_enabled", True)), save=False)
            self._timeline_update_range_button_tooltips()

            material_btn = QtWidgets.QPushButton(panel)
            material_btn.setObjectName("GLTimelineMaterialLiveButton")
            material_btn.setCheckable(True)
            material_btn.setFixedSize(26, 26)
            material_btn.setFlat(True)
            material_btn.toggled.connect(self._timeline_on_material_live_toggled)
            header.addWidget(material_btn, 0)
            self._timeline_material_live_btn = material_btn
            self._update_timeline_material_live_button()

            seed_lbl = QtWidgets.QLabel("Seed", panel)
            header.addWidget(seed_lbl, 0)

            seed_spin = QtWidgets.QSpinBox(panel)
            seed_spin.setRange(0, 999999)
            seed_spin.setValue(int(getattr(self, "_timeline_texture_seed", 0) or 0))
            seed_spin.setFixedWidth(92)
            seed_spin.setToolTip("Shared procedural texture seed for the scene view.")
            seed_spin.valueChanged.connect(self._timeline_on_texture_seed_changed)
            header.addWidget(seed_spin, 0)
            self._timeline_texture_seed_spin = seed_spin

            curves_btn = QtWidgets.QPushButton("Curves", panel)
            curves_btn.setObjectName("GLTimelineCurvesButton")
            curves_btn.setCheckable(True)
            curves_btn.setFixedSize(26, 26)
            curves_btn.setFlat(True)
            curves_btn.toggled.connect(self._timeline_on_curves_toggled)
            header.addWidget(curves_btn, 0)
            self._timeline_curves_btn = curves_btn
            self._update_timeline_curves_button()

            straight_btn = QtWidgets.QPushButton(panel)
            straight_btn.setObjectName("GLTimelineHandleStraightButton")
            straight_btn.setCheckable(True)
            straight_btn.setFixedSize(26, 26)
            straight_btn.setFlat(True)
            straight_btn.clicked.connect(self._timeline_on_handle_straight_clicked)
            header.addWidget(straight_btn, 0)
            self._timeline_handle_straight_btn = straight_btn

            tied_btn = QtWidgets.QPushButton(panel)
            tied_btn.setObjectName("GLTimelineHandleTiedButton")
            tied_btn.setCheckable(True)
            tied_btn.setFixedSize(26, 26)
            tied_btn.setFlat(True)
            tied_btn.clicked.connect(self._timeline_on_handle_tied_clicked)
            header.addWidget(tied_btn, 0)
            self._timeline_handle_tied_btn = tied_btn

            untied_btn = QtWidgets.QPushButton(panel)
            untied_btn.setObjectName("GLTimelineHandleUntiedButton")
            untied_btn.setCheckable(True)
            untied_btn.setFixedSize(26, 26)
            untied_btn.setFlat(True)
            untied_btn.clicked.connect(self._timeline_on_handle_untied_clicked)
            header.addWidget(untied_btn, 0)
            self._timeline_handle_untied_btn = untied_btn
            self._timeline_update_handle_mode_buttons()

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
            left_header_layout = QtWidgets.QHBoxLayout(left_header_spacer)
            left_header_layout.setContentsMargins(0, 0, 6, 0)
            left_header_layout.setSpacing(0)
            left_header_layout.addStretch(1)
            target_lbl = QtWidgets.QLabel("", left_header_spacer)
            target_lbl.setObjectName("GLTimelineTargetLabel")
            target_lbl.setStyleSheet(
                "QLabel#GLTimelineTargetLabel{color:#94a3b8;font-size:10px;font-weight:600;background:transparent;}"
            )
            target_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            target_lbl.setToolTip("")
            left_header_layout.addWidget(target_lbl, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            tracks_grid.addWidget(left_header_spacer, 0, 0, 1, 2)
            self._timeline_left_header_spacer = left_header_spacer
            self._timeline_target_label = target_lbl
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
                        in_frame = getattr(self._view, "_timeline_in_frame", None)
                        out_frame = getattr(self._view, "_timeline_out_frame", None)
                        in_local = None
                        out_local = None
                        try:
                            if in_frame is not None:
                                in_local = int(int(in_frame) - int(start))
                        except Exception:
                            in_local = None
                        try:
                            if out_frame is not None:
                                out_local = int(int(out_frame) - int(start))
                        except Exception:
                            out_local = None
                        for value in range(vmin, vmax + 1):
                            vv = int(value)
                            if (in_local is not None and vv == int(in_local)) or (
                                out_local is not None and vv == int(out_local)
                            ):
                                # Range markers own this column; skip dash to avoid a doubled "|" look.
                                continue
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
                        def _draw_range_marker(abs_frame, is_in: bool, color: QtGui.QColor):
                            try:
                                if abs_frame is None:
                                    return
                                frame_abs = int(abs_frame)
                                local = int(frame_abs - start)
                                if local < int(vmin) or local > int(vmax):
                                    return
                                xx = self._view._timeline_slider_value_to_x(int(local), slider=self)
                                if xx is None:
                                    return
                                x_line = int(round(float(xx)))
                                half_h = int(max(6, min(9, int((self.height() - 6) * 0.5))))
                                arm_len = int(max(4, min(8, half_h - 1)))
                                y_top = int(max(2, y_mid - half_h))
                                y_bot = int(min(self.height() - 3, y_mid + half_h))
                                x_line = int(max(1, min(x_line, self.width() - 2)))
                                if y_bot <= y_top:
                                    return
                                p.save()
                                try:
                                    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                                except Exception:
                                    pass
                                pen = QtGui.QPen(color, 2.2, QtCore.Qt.SolidLine, QtCore.Qt.FlatCap, QtCore.Qt.MiterJoin)
                                p.setPen(pen)
                                if bool(is_in):
                                    x_arm = int(min(self.width() - 2, x_line + arm_len))
                                    p.drawLine(int(x_line), int(y_top), int(x_line), int(y_bot))
                                    p.drawLine(int(x_line), int(y_top), int(x_arm), int(y_top))
                                    p.drawLine(int(x_line), int(y_bot), int(x_arm), int(y_bot))
                                else:
                                    x_arm = int(max(1, x_line - arm_len))
                                    p.drawLine(int(x_line), int(y_top), int(x_line), int(y_bot))
                                    p.drawLine(int(x_arm), int(y_top), int(x_line), int(y_top))
                                    p.drawLine(int(x_arm), int(y_bot), int(x_line), int(y_bot))
                                p.restore()
                            except Exception:
                                return

                        # Draw range brackets above frame dashes.
                        _draw_range_marker(in_frame, True, QtGui.QColor(56, 189, 248, 240))
                        _draw_range_marker(out_frame, False, QtGui.QColor(250, 204, 21, 240))
                        # Keep the custom keyframe handle icon above dashes and range brackets.
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
                    self._handle_drag = None
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
                    try:
                        self._view._timeline_update_handle_mode_buttons()
                    except Exception:
                        pass

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

                def _selected_handle_points(self, pts_by_axis):
                    out = []
                    try:
                        start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                    except Exception:
                        start = 0
                    selected = self._selected_set()
                    if not selected:
                        return out
                    by_key = {}
                    for axis, rows in pts_by_axis.items():
                        for frame, val, x, y in rows:
                            by_key[(int(axis), int(frame))] = (float(val), float(x), float(y))
                    for axis, frame in sorted(selected, key=lambda af: (int(af[0]), int(af[1]))):
                        if not bool(self._view._timeline_axis_is_visible(int(axis))):
                            continue
                        key = (int(axis), int(frame))
                        if key not in by_key:
                            continue
                        val, xk, yk = by_key[key]
                        h = self._view._timeline_axis_handles_for_key(int(axis), int(frame), create=False)
                        if not isinstance(h, dict):
                            h = self._view._timeline_default_axis_handles(
                                int(axis),
                                int(frame),
                                key_value=float(val),
                            )
                        try:
                            mode = str(h.get("mode", "tied") or "tied").strip().lower()
                        except Exception:
                            mode = "tied"
                        if mode == "straight":
                            continue
                        try:
                            in_dx, in_dy = tuple(h.get("in", (-3.0, 0.0)))
                        except Exception:
                            in_dx, in_dy = (-3.0, 0.0)
                        try:
                            out_dx, out_dy = tuple(h.get("out", (3.0, 0.0)))
                        except Exception:
                            out_dx, out_dy = (3.0, 0.0)
                        xin = self._view._timeline_local_frame_to_tracks_x_float((float(frame) - float(start)) + float(in_dx))
                        xout = self._view._timeline_local_frame_to_tracks_x_float((float(frame) - float(start)) + float(out_dx))
                        if xin is None or xout is None:
                            continue
                        yin = self._value_to_y(float(val) + float(in_dy))
                        yout = self._value_to_y(float(val) + float(out_dy))
                        out.append(
                            {
                                "axis": int(axis),
                                "frame": int(frame),
                                "value": float(val),
                                "key": (float(xk), float(yk)),
                                "in": (float(xin), float(yin)),
                                "out": (float(xout), float(yout)),
                            }
                        )
                    return out

                def _nearest_handle(self, posf):
                    pts_by_axis = self._axis_points()
                    handles = self._selected_handle_points(pts_by_axis)
                    if not handles:
                        return None
                    px = float(posf.x())
                    py = float(posf.y())
                    best = None
                    best_d2 = None
                    for item in handles:
                        for side in ("in", "out"):
                            hx, hy = item.get(side, (None, None))
                            if hx is None or hy is None:
                                continue
                            dx = float(hx) - px
                            dy = float(hy) - py
                            d2 = (dx * dx) + (dy * dy)
                            if d2 > 64.0:
                                continue
                            if best is None or best_d2 is None or d2 < best_d2:
                                best = (
                                    int(item.get("axis", 0)),
                                    int(item.get("frame", 0)),
                                    str(side),
                                    float(item.get("value", 0.0)),
                                )
                                best_d2 = d2
                    return best

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
                    try:
                        start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                    except Exception:
                        start = 0
                    selected = self._selected_set()
                    for axis in range(6):
                        rows = pts_by_axis.get(axis, [])
                        if not rows:
                            continue
                        col = self._view._timeline_axis_color(axis)
                        path = QtGui.QPainterPath()
                        path.moveTo(float(rows[0][2]), float(rows[0][3]))
                        if len(rows) > 1:
                            for i in range(len(rows) - 1):
                                f1, v1, x1, y1 = rows[i]
                                f2, v2, x2, y2 = rows[i + 1]
                                h1 = self._view._timeline_axis_handles_for_key(int(axis), int(f1), create=False)
                                if not isinstance(h1, dict):
                                    h1 = self._view._timeline_default_axis_handles(
                                        int(axis),
                                        int(f1),
                                        key_value=float(v1),
                                    )
                                h2 = self._view._timeline_axis_handles_for_key(int(axis), int(f2), create=False)
                                if not isinstance(h2, dict):
                                    h2 = self._view._timeline_default_axis_handles(
                                        int(axis),
                                        int(f2),
                                        key_value=float(v2),
                                    )
                                try:
                                    mode1 = str(h1.get("mode", "tied") or "tied").strip().lower()
                                except Exception:
                                    mode1 = "tied"
                                try:
                                    mode2 = str(h2.get("mode", "tied") or "tied").strip().lower()
                                except Exception:
                                    mode2 = "tied"
                                if mode1 == "straight" or mode2 == "straight":
                                    path.lineTo(QtCore.QPointF(float(x2), float(y2)))
                                    continue
                                try:
                                    out_dx, out_dy = tuple(h1.get("out", (3.0, 0.0)))
                                except Exception:
                                    out_dx, out_dy = (3.0, 0.0)
                                try:
                                    in_dx, in_dy = tuple(h2.get("in", (-3.0, 0.0)))
                                except Exception:
                                    in_dx, in_dy = (-3.0, 0.0)
                                c1x = self._view._timeline_local_frame_to_tracks_x_float((float(f1) - float(start)) + float(out_dx))
                                c2x = self._view._timeline_local_frame_to_tracks_x_float((float(f2) - float(start)) + float(in_dx))
                                if c1x is None:
                                    c1x = float(x1)
                                if c2x is None:
                                    c2x = float(x2)
                                c1y = self._value_to_y(float(v1) + float(out_dy))
                                c2y = self._value_to_y(float(v2) + float(in_dy))
                                path.cubicTo(
                                    QtCore.QPointF(float(c1x), float(c1y)),
                                    QtCore.QPointF(float(c2x), float(c2y)),
                                    QtCore.QPointF(float(x2), float(y2)),
                                )
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
                    try:
                        handle_items = self._selected_handle_points(pts_by_axis)
                    except Exception:
                        handle_items = []
                    if handle_items:
                        for item in handle_items:
                            kx, ky = item.get("key", (0.0, 0.0))
                            axis_idx = int(item.get("axis", 0))
                            hcol = self._view._timeline_axis_color(axis_idx)
                            for side in ("in", "out"):
                                hx, hy = item.get(side, (None, None))
                                if hx is None or hy is None:
                                    continue
                                p.setPen(QtGui.QPen(QtGui.QColor(148, 163, 184, 220), 1))
                                p.setBrush(QtCore.Qt.NoBrush)
                                p.drawLine(
                                    QtCore.QPointF(float(kx), float(ky)),
                                    QtCore.QPointF(float(hx), float(hy)),
                                )
                                p.setPen(QtGui.QPen(QtGui.QColor(15, 23, 42, 220), 1))
                                p.setBrush(QtGui.QBrush(hcol))
                                p.drawEllipse(QtCore.QPointF(float(hx), float(hy)), 3.4, 3.4)
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
                    handle_hit = self._nearest_handle(posf)
                    if handle_hit is not None:
                        axis, frame, side, value = handle_hit
                        self._selecting = False
                        self._drag = None
                        self._handle_drag = {
                            "axis": int(axis),
                            "frame": int(frame),
                            "side": str(side),
                            "value": float(value),
                            "dirty": False,
                        }
                        self.update()
                        ev.accept()
                        return
                    hit = self._nearest_point(posf)
                    if hit is None:
                        self._handle_drag = None
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
                    self._handle_drag = None
                    self._set_selected_set(drag_sel)
                    self.update()
                    ev.accept()

                def mouseMoveEvent(self, ev):
                    if isinstance(self._handle_drag, dict):
                        posf = self._event_pos(ev)
                        axis = int(self._handle_drag.get("axis", 0))
                        frame = int(self._handle_drag.get("frame", 0))
                        side = str(self._handle_drag.get("side", "out"))
                        value = float(self._handle_drag.get("value", 0.0))
                        try:
                            start = int(max(0, int(getattr(self._view, "_timeline_view_start", 0) or 0)))
                        except Exception:
                            start = 0
                        local_frame = float(frame) - float(start)
                        kx = self._view._timeline_local_frame_to_tracks_x_float(local_frame)
                        if kx is None:
                            return
                        ky = self._value_to_y(float(value))
                        px_per_frame = self._view._timeline_tracks_pixels_per_frame(local_frame)
                        if abs(float(px_per_frame)) <= 1.0e-6:
                            px_per_frame = 1.0
                        dx_frame = (float(posf.x()) - float(kx)) / float(px_per_frame)
                        dy_value = self._y_to_value(float(posf.y())) - float(value)
                        if side == "in":
                            dx_frame = -abs(float(dx_frame))
                        else:
                            dx_frame = abs(float(dx_frame))
                        changed = bool(
                            self._view._timeline_set_axis_handle_value(
                                int(axis),
                                int(frame),
                                str(side),
                                float(dx_frame),
                                float(dy_value),
                                commit=False,
                            )
                        )
                        if changed:
                            self._handle_drag["dirty"] = True
                        self.update()
                        ev.accept()
                        return
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
                    if isinstance(self._handle_drag, dict):
                        try:
                            if bool(self._handle_drag.get("dirty", False)):
                                self._view._timeline_save_to_disk()
                        except Exception:
                            pass
                        self._handle_drag = None
                        try:
                            self._view._timeline_apply_frame_if_keyed(
                                int(self._view._timeline_current_frame()),
                                force=True,
                            )
                        except Exception:
                            pass
                        self.update()
                        ev.accept()
                        return
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
            try:
                self._build_timeline_audio_panel()
            except Exception:
                pass
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
            self._timeline_mark_in_btn = None
            self._timeline_mark_out_btn = None
            self._timeline_loop_btn = None
            self._timeline_curves_btn = None
            self._timeline_material_live_btn = None
            self._timeline_handle_straight_btn = None
            self._timeline_handle_tied_btn = None
            self._timeline_handle_untied_btn = None
            self._timeline_frame_slider = None
            self._timeline_frame_spin = None
            self._timeline_key_count_label = None
            self._timeline_tick_labels = []
            self._timeline_target_label = None
            self._timeline_ticks_frame = None

    def timeline_visible(self) -> bool:
        return bool(getattr(self, "_timeline_enabled", False))

    def set_timeline_visible(self, visible: bool) -> None:
        want = bool(visible)
        self._build_timeline_panel()
        try:
            self._build_timeline_audio_panel()
        except Exception:
            pass
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
                try:
                    hook = getattr(self, "_timeline_audio_on_timeline_play_toggled", None)
                    if callable(hook):
                        hook(False)
                except Exception:
                    pass
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
            try:
                self._update_timeline_loop_button()
            except Exception:
                pass
            try:
                self._timeline_update_range_button_tooltips()
            except Exception:
                pass
            try:
                self._timeline_update_range_marker_visuals()
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
                hook = getattr(self, "_timeline_audio_on_timeline_play_toggled", None)
                if callable(hook):
                    hook(False)
            except Exception:
                pass
        try:
            self.update()
        except Exception:
            pass

    def timeline_audio_visible(self) -> bool:
        return bool(getattr(self, "_timeline_audio_enabled", False))

    def set_timeline_audio_visible(self, visible: bool) -> None:
        want = bool(visible)
        try:
            self._build_timeline_audio_panel()
        except Exception:
            pass
        if want == bool(getattr(self, "_timeline_audio_enabled", False)):
            panel = getattr(self, "_timeline_audio_panel", None)
            if panel is not None:
                panel.setVisible(want)
                self._layout_timeline_panel()
            if not want:
                try:
                    self._timeline_audio_on_timeline_play_toggled(False)
                except Exception:
                    pass
            return
        self._timeline_audio_enabled = want
        panel = getattr(self, "_timeline_audio_panel", None)
        if panel is not None:
            panel.setVisible(want)
        if want:
            try:
                self.set_timeline_scene_context()
            except Exception:
                pass
            try:
                timer = getattr(self, "_timeline_play_timer", None)
                playing = bool(timer is not None and timer.isActive())
                if bool(getattr(self, "_timeline_enabled", False)) and playing:
                    self._timeline_audio_on_timeline_play_toggled(True)
            except Exception:
                pass
            self._timeline_audio_update_playhead()
        else:
            try:
                self._timeline_audio_on_timeline_play_toggled(False)
            except Exception:
                pass
        self._layout_timeline_panel()
        try:
            self.update()
        except Exception:
            pass

    def _timeline_audio_format_ms(self, ms: int) -> str:
        try:
            total = max(0, int(ms) // 1000)
        except Exception:
            total = 0
        hh = total // 3600
        mm = (total % 3600) // 60
        ss = total % 60
        if hh > 0:
            return f"{hh}:{mm:02d}:{ss:02d}"
        return f"{mm}:{ss:02d}"

    def _timeline_audio_status(self, text: str) -> None:
        lbl = getattr(self, "_timeline_audio_status_label", None)
        if lbl is None:
            return
        raw = str(text or "").strip()
        changed = False
        try:
            changed = bool(lbl.isVisible()) != bool(raw)
        except Exception:
            changed = False
        try:
            lbl.setText(raw)
            lbl.setVisible(bool(raw))
        except Exception:
            pass
        if changed:
            try:
                self._layout_timeline_panel()
            except Exception:
                pass

    def _timeline_audio_update_file_label(self) -> None:
        lbl = getattr(self, "_timeline_audio_file_label", None)
        if lbl is None:
            return
        path_text = str(getattr(self, "_timeline_audio_path", "") or "").strip()
        if not path_text:
            try:
                lbl.setText("No audio selected")
                lbl.setToolTip("")
            except Exception:
                pass
            return
        try:
            name = Path(path_text).name
        except Exception:
            name = path_text
        try:
            duration_ms = int(getattr(self, "_timeline_audio_duration_ms", 0) or 0)
        except Exception:
            duration_ms = 0
        suffix = f" ({self._timeline_audio_format_ms(duration_ms)})" if duration_ms > 0 else ""
        try:
            lbl.setText(f"{name}{suffix}")
            lbl.setToolTip(path_text)
        except Exception:
            pass

    def _timeline_audio_set_muted(self, muted: bool, save: bool = True) -> None:
        self._timeline_audio_apply_mute(bool(muted), save=bool(save))

    def _timeline_audio_apply_mute(self, muted: bool, save: bool = True) -> None:
        want = bool(muted)
        self._timeline_audio_muted = want
        output = getattr(self, "_timeline_audio_output", None)
        player = getattr(self, "_timeline_audio_player", None)
        try:
            if output is not None and hasattr(output, "setMuted"):
                output.setMuted(want)
            elif player is not None and hasattr(player, "setMuted"):
                player.setMuted(want)
            elif output is not None and hasattr(output, "setVolume"):
                output.setVolume(0.0 if want else 1.0)
            elif player is not None and hasattr(player, "setVolume"):
                player.setVolume(0 if want else 100)
        except Exception:
            pass
        self._timeline_audio_update_mute_button()
        if bool(save):
            try:
                self._timeline_audio_save_to_disk()
            except Exception:
                pass

    def _timeline_audio_update_mute_button(self) -> None:
        btn = getattr(self, "_timeline_audio_mute_btn", None)
        if btn is None:
            return
        muted = bool(getattr(self, "_timeline_audio_muted", False))
        text = "Unmute" if muted else "Mute"
        tooltip = "Unmute audio playback" if muted else "Mute audio playback"
        try:
            btn.blockSignals(True)
            btn.setChecked(muted)
            btn.setText(text)
            btn.setToolTip(tooltip)
        except Exception:
            pass
        finally:
            try:
                btn.blockSignals(False)
            except Exception:
                pass

    def _timeline_audio_on_mute_toggled(self, checked: bool) -> None:
        self._timeline_audio_apply_mute(bool(checked), save=True)

    def _timeline_audio_init_player(self) -> bool:
        if getattr(self, "_timeline_audio_player", None) is not None:
            return True
        if QtMultimedia is None or not hasattr(QtMultimedia, "QMediaPlayer"):
            return False
        try:
            player = QtMultimedia.QMediaPlayer(self)
        except Exception:
            player = None
        if player is None:
            return False
        output = None
        try:
            if hasattr(QtMultimedia, "QAudioOutput"):
                output = QtMultimedia.QAudioOutput(self)
                try:
                    output.setVolume(1.0)
                except Exception:
                    pass
                try:
                    player.setAudioOutput(output)
                except Exception:
                    pass
        except Exception:
            output = None
        try:
            if hasattr(player, "durationChanged"):
                player.durationChanged.connect(self._timeline_audio_on_media_duration_changed)
            if hasattr(player, "errorOccurred"):
                player.errorOccurred.connect(self._timeline_audio_on_player_error)
            elif hasattr(player, "error"):
                player.error.connect(self._timeline_audio_on_player_error)
        except Exception:
            pass
        self._timeline_audio_player = player
        self._timeline_audio_output = output
        try:
            self._timeline_audio_apply_mute(bool(getattr(self, "_timeline_audio_muted", False)), save=False)
        except Exception:
            pass
        return True

    def _timeline_audio_set_media_source(self, path: Path) -> bool:
        if not self._timeline_audio_init_player():
            return False
        player = getattr(self, "_timeline_audio_player", None)
        if player is None:
            return False
        url = QtCore.QUrl.fromLocalFile(str(path))
        loaded = False
        try:
            player.stop()
        except Exception:
            pass
        try:
            player.setSource(url)
            loaded = True
        except Exception:
            try:
                media_content = QtMultimedia.QMediaContent(url)
                player.setMedia(media_content)
                loaded = True
            except Exception:
                loaded = False
        if loaded:
            try:
                player.pause()
            except Exception:
                pass
            try:
                self._timeline_audio_apply_mute(bool(getattr(self, "_timeline_audio_muted", False)), save=False)
            except Exception:
                pass
            try:
                self._timeline_audio_on_media_duration_changed(int(player.duration() or 0))
            except Exception:
                pass
        return bool(loaded)

    def _timeline_audio_load_waveform(self, path: Path) -> tuple[list[float], int]:
        ext = str(path.suffix or "").strip().lower()
        if ext == ".wav":
            return _audio_waveform_from_wav(path, bins=1200)
        return (_audio_waveform_from_file_bytes(path, bins=1200), 0)

    def _timeline_audio_seek_ms(self, ms: int) -> None:
        player = getattr(self, "_timeline_audio_player", None)
        if player is None:
            return
        target = max(0, int(ms))
        try:
            dur = int(getattr(self, "_timeline_audio_duration_ms", 0) or 0)
        except Exception:
            dur = 0
        if dur > 0:
            target = max(0, min(int(dur), int(target)))
        try:
            player.setPosition(int(target))
        except Exception:
            pass

    def _timeline_audio_target_ms_from_frame(self, frame: int) -> int:
        try:
            fps = float(getattr(self, "_timeline_fps", 24.0) or 24.0)
        except Exception:
            fps = 24.0
        if fps <= 1.0e-6:
            fps = 24.0
        try:
            ff = max(0.0, float(frame))
        except Exception:
            ff = 0.0
        return int(round((ff / fps) * 1000.0))

    def _timeline_audio_update_playhead(self, frame: int | None = None) -> None:
        wave = getattr(self, "_timeline_audio_wave_widget", None)
        if wave is None:
            return
        if frame is None:
            try:
                frame = int(self._timeline_current_frame())
            except Exception:
                frame = 0
        ms = self._timeline_audio_target_ms_from_frame(int(frame))
        try:
            dur = int(getattr(self, "_timeline_audio_duration_ms", 0) or 0)
        except Exception:
            dur = 0
        norm = 0.0 if dur <= 0 else float(ms) / float(max(1, dur))
        try:
            wave.setPlayhead(max(0.0, min(1.0, norm)))
        except Exception:
            pass

    def _timeline_audio_set_path(self, path: str, save: bool = True) -> None:
        raw = str(path or "").strip()
        norm = ""
        missing = False
        if raw:
            try:
                p = Path(raw).expanduser()
            except Exception:
                p = None
            if p is not None:
                if not p.is_absolute():
                    try:
                        p = self._timeline_default_project_dir() / p
                    except Exception:
                        p = Path(raw)
                try:
                    p = p.resolve()
                except Exception:
                    pass
                if p.is_file():
                    norm = p.as_posix()
                else:
                    missing = True
            else:
                missing = True
        self._timeline_audio_path = norm
        self._timeline_audio_loaded_key = norm or None
        samples: list[float] = []
        duration_ms = 0
        if norm:
            try:
                samples, duration_ms = self._timeline_audio_load_waveform(Path(norm))
            except Exception:
                samples, duration_ms = ([], 0)
        self._timeline_audio_wave_samples = list(samples or [])
        self._timeline_audio_duration_ms = int(max(0, duration_ms))
        wave = getattr(self, "_timeline_audio_wave_widget", None)
        if wave is not None:
            try:
                wave.setWaveform(self._timeline_audio_wave_samples)
            except Exception:
                pass
        if not norm:
            try:
                player = getattr(self, "_timeline_audio_player", None)
                if player is not None:
                    player.stop()
            except Exception:
                pass
            self._timeline_audio_status("Audio file not found." if missing else "")
            self._timeline_audio_update_file_label()
            self._timeline_audio_update_playhead()
            if bool(save):
                try:
                    self._timeline_audio_save_to_disk()
                except Exception:
                    pass
            return
        loaded = self._timeline_audio_set_media_source(Path(norm))
        if not loaded:
            self._timeline_audio_status("Audio playback backend unavailable for this file.")
        else:
            self._timeline_audio_status("")
        self._timeline_audio_update_file_label()
        self._timeline_audio_update_playhead()
        try:
            self._timeline_audio_on_timeline_frame_changed(int(self._timeline_current_frame()), playing=False)
        except Exception:
            pass
        if bool(save):
            try:
                self._timeline_audio_save_to_disk()
            except Exception:
                pass

    def _timeline_audio_on_media_duration_changed(self, duration) -> None:
        try:
            dur = max(0, int(duration))
        except Exception:
            dur = 0
        if dur > 0:
            self._timeline_audio_duration_ms = int(dur)
        self._timeline_audio_update_file_label()
        self._timeline_audio_update_playhead()

    def _timeline_audio_on_player_error(self, *_args) -> None:
        player = getattr(self, "_timeline_audio_player", None)
        msg = ""
        if player is not None:
            try:
                msg = str(player.errorString() or "")
            except Exception:
                msg = ""
        self._timeline_audio_status(msg or "Failed to load audio file.")

    def _timeline_audio_on_browse_clicked(self) -> None:
        current = str(getattr(self, "_timeline_audio_path", "") or "").strip()
        start = None
        if current:
            try:
                cp = Path(current)
                start = cp.parent if cp.is_file() else cp
            except Exception:
                start = None
        if start is None:
            try:
                start = self._timeline_default_project_dir()
            except Exception:
                start = Path.home()
        chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load Timeline Audio",
            str(start),
            "Audio Files (*.wav *.mp3 *.ogg *.flac *.aac *.m4a);;All Files (*.*)",
        )
        if not chosen:
            return
        self._timeline_audio_set_path(chosen, save=True)

    def _timeline_audio_on_clear_clicked(self) -> None:
        self._timeline_audio_set_path("", save=True)

    def _timeline_audio_on_timeline_play_toggled(self, playing: bool) -> None:
        if (not bool(getattr(self, "_timeline_audio_enabled", False))) and bool(playing):
            return
        player = getattr(self, "_timeline_audio_player", None)
        if player is None:
            return
        path_text = str(getattr(self, "_timeline_audio_path", "") or "").strip()
        if not path_text:
            return
        frame = 0
        try:
            frame = int(self._timeline_current_frame())
        except Exception:
            frame = 0
        target_ms = self._timeline_audio_target_ms_from_frame(int(frame))
        if bool(playing):
            self._timeline_audio_seek_ms(target_ms)
            try:
                player.play()
            except Exception:
                pass
        else:
            try:
                player.pause()
            except Exception:
                pass
        self._timeline_audio_update_playhead(frame)

    def _timeline_audio_on_timeline_frame_changed(self, frame: int, playing: bool = False) -> None:
        if not bool(getattr(self, "_timeline_audio_enabled", False)):
            return
        target_ms = self._timeline_audio_target_ms_from_frame(int(frame))
        player = getattr(self, "_timeline_audio_player", None)
        if player is not None and str(getattr(self, "_timeline_audio_path", "") or "").strip():
            if bool(playing):
                cur = None
                try:
                    cur = int(player.position() or 0)
                except Exception:
                    cur = None
                if cur is None or abs(int(cur) - int(target_ms)) > 90:
                    self._timeline_audio_seek_ms(target_ms)
            else:
                self._timeline_audio_seek_ms(target_ms)
        self._timeline_audio_update_playhead(int(frame))

    def _build_timeline_audio_panel(self) -> None:
        existing = getattr(self, "_timeline_audio_panel", None)
        if existing is not None:
            has_wave = isinstance(getattr(self, "_timeline_audio_wave_widget", None), _TimelineAudioWaveformWidget)
            has_label = isinstance(getattr(self, "_timeline_audio_file_label", None), QtWidgets.QLabel)
            has_status = isinstance(getattr(self, "_timeline_audio_status_label", None), QtWidgets.QLabel)
            has_mute = isinstance(getattr(self, "_timeline_audio_mute_btn", None), QtWidgets.QPushButton)
            if has_wave and has_label and has_status and has_mute:
                self._timeline_audio_update_mute_button()
                return
            try:
                existing.hide()
                existing.deleteLater()
            except Exception:
                pass
        panel = QtWidgets.QFrame(self)
        panel.setObjectName("GLTimelineAudioPanel")
        panel.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        panel.setStyleSheet(
            "".join((
                "#GLTimelineAudioPanel{background:rgba(9,14,24,225);border-top:1px solid #334155;}",
                "#GLTimelineAudioPanel QLabel{color:#e2e8f0;font-size:11px;}",
                "#GLTimelineAudioPanel QPushButton{padding:2px 8px;font-weight:600;color:#e2e8f0;background:#1f2937;border-radius:4px;}",
                "#GLTimelineAudioPanel QPushButton:hover{background:#334155;}",
                "#GLTimelineAudioPanel QPushButton:checked{background:#14532d;}",
                "#GLTimelineAudioPanel QLabel#GLTimelineAudioStatus{color:#94a3b8;font-size:10px;}",
            ))
        )
        root = QtWidgets.QVBoxLayout(panel)
        root.setContentsMargins(10, 4, 10, 4)
        root.setSpacing(2)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        title = QtWidgets.QLabel("Audio", panel)
        row.addWidget(title, 0)

        load_btn = QtWidgets.QPushButton("Load Audio", panel)
        load_btn.setFixedHeight(22)
        load_btn.clicked.connect(self._timeline_audio_on_browse_clicked)
        row.addWidget(load_btn, 0)

        clear_btn = QtWidgets.QPushButton("Clear", panel)
        clear_btn.setFixedHeight(22)
        clear_btn.clicked.connect(self._timeline_audio_on_clear_clicked)
        row.addWidget(clear_btn, 0)

        mute_btn = QtWidgets.QPushButton("Mute", panel)
        mute_btn.setFixedHeight(22)
        mute_btn.setCheckable(True)
        mute_btn.toggled.connect(self._timeline_audio_on_mute_toggled)
        row.addWidget(mute_btn, 0)

        file_lbl = QtWidgets.QLabel("No audio selected", panel)
        file_lbl.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        file_lbl.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        row.addWidget(file_lbl, 1)

        root.addLayout(row, 0)

        wave = _TimelineAudioWaveformWidget(panel)
        root.addWidget(wave, 1)

        status = QtWidgets.QLabel("", panel)
        status.setObjectName("GLTimelineAudioStatus")
        status.setVisible(False)
        root.addWidget(status, 0)

        panel.setVisible(bool(getattr(self, "_timeline_audio_enabled", False)))
        self._timeline_audio_panel = panel
        self._timeline_audio_file_label = file_lbl
        self._timeline_audio_mute_btn = mute_btn
        self._timeline_audio_wave_widget = wave
        self._timeline_audio_status_label = status
        try:
            wave.setWaveform(getattr(self, "_timeline_audio_wave_samples", []) or [])
        except Exception:
            pass
        self._timeline_audio_update_mute_button()
        self._timeline_audio_update_file_label()
        self._timeline_audio_update_playhead()


