from .spec import SCENE_SPEC

_DEBUG_PATCH_KEY = "_scene_header_debug_patch_v1"
_TIMELINE_EDGE_PATCH_KEY = "_scene_timeline_edge_pan_patch_v1"
_TIMELINE_EDGE_FILTER_ATTR = "_timeline_edge_pan_filter_v1"


def _patch_header_debug_button() -> None:
    try:
        from echograph.ui import node_item as node_item_mod
    except Exception:
        return

    cls = getattr(node_item_mod, "NodeItem", None)
    if cls is None or getattr(cls, _DEBUG_PATCH_KEY, False):
        return

    original = getattr(cls, "_header_debug_button_kind", None)
    if not callable(original):
        return

    def wrapped(self, *args, **kwargs):
        try:
            kind = str(getattr(getattr(self, "model", None), "kind", "") or "").strip().lower()
            if kind in ("scene", "scene_assembly", "scene_outliner"):
                return "scene"
        except Exception:
            pass
        try:
            return original(self, *args, **kwargs)
        except Exception:
            return None

    try:
        cls._header_debug_button_kind = wrapped
        setattr(cls, _DEBUG_PATCH_KEY, True)
    except Exception:
        return


def _patch_timeline_frame_slider_edge_pan() -> None:
    try:
        from echograph.qt_compat import QtCore
        from echograph.ui import gl_view_timeline_widgets as timeline_widgets
    except Exception:
        return

    cls = getattr(timeline_widgets, "GraphGLTimelineWidgetsMixin", None)
    if cls is None or getattr(cls, _TIMELINE_EDGE_PATCH_KEY, False):
        return

    original = getattr(cls, "_build_timeline_panel", None)
    if not callable(original):
        return

    class _TimelineEdgePanFilter(QtCore.QObject):
        def __init__(self, slider, view):
            super().__init__(slider)
            self._slider = slider
            self._view = view

        @staticmethod
        def _left_button_down(ev) -> bool:
            try:
                return bool(ev.buttons() & QtCore.Qt.LeftButton)
            except Exception:
                pass
            try:
                return bool(ev.buttons() & QtCore.Qt.MouseButton.LeftButton)
            except Exception:
                pass
            return False

        @staticmethod
        def _event_x(ev):
            try:
                if hasattr(ev, "position"):
                    posf = ev.position()
                    if posf is not None:
                        return int(posf.x())
            except Exception:
                pass
            try:
                if hasattr(ev, "pos"):
                    pos = ev.pos()
                    if pos is not None:
                        return int(pos.x())
            except Exception:
                pass
            return None

        def _pixel_x_to_value(self, x_pos: int):
            slider = self._slider
            if slider is None:
                return None
            helper = getattr(slider, "_pixel_x_to_value", None)
            if callable(helper):
                try:
                    return helper(int(x_pos))
                except Exception:
                    pass
            try:
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
                pos = int(round(float(x_pos) - float(groove.left()) - (float(slider_len) * 0.5)))
                pos = max(0, min(span, pos))
                val = int(
                    QtWidgets.QStyle.sliderValueFromPosition(
                        int(slider.minimum()),
                        int(slider.maximum()),
                        int(pos),
                        int(span),
                        bool(getattr(opt, "upsideDown", False)),
                    )
                )
                lo = int(slider.minimum())
                hi = int(slider.maximum())
                return max(int(lo), min(int(hi), int(val)))
            except Exception:
                return None

        def _auto_pan_target(self, x_pos: int, target: int):
            slider = self._slider
            view = self._view
            if slider is None or view is None:
                return None
            try:
                lo = int(slider.minimum())
                hi = int(slider.maximum())
                cur = int(slider.sliderPosition())
            except Exception:
                return None
            if hi <= lo:
                return None
            try:
                width = int(max(1, slider.width()))
            except Exception:
                width = 1
            edge_px = 8
            right_edge = int(width - 1 - edge_px)
            overflow_left = int(max(0, int(edge_px) - int(x_pos)))
            overflow_right = int(max(0, int(x_pos) - int(right_edge)))
            direction = 0
            overflow = 0
            if int(target) <= int(lo) and int(cur) <= int(lo) and overflow_left > 0:
                direction = -1
                overflow = int(overflow_left)
            elif int(target) >= int(hi) and int(cur) >= int(hi) and overflow_right > 0:
                direction = 1
                overflow = int(overflow_right)
            if direction == 0:
                return None
            try:
                total_fn = getattr(view, "_timeline_max_known_frame", None)
                total = int(max(0, total_fn())) if callable(total_fn) else int(getattr(view, "_timeline_total_max", 240) or 240)
            except Exception:
                total = 240
            try:
                span = int(getattr(view, "_timeline_view_span", 120) or 120)
            except Exception:
                span = 120
            span = max(24, int(span))
            span = min(int(span), int(total)) if int(total) > 0 else int(span)
            start_max = max(0, int(total) - int(span))
            try:
                start = int(getattr(view, "_timeline_view_start", 0) or 0)
            except Exception:
                start = 0
            step = max(1, int(1 + (float(max(0, overflow)) / 16.0)))
            if direction > 0:
                step = min(int(step), max(0, int(start_max) - int(start)))
            else:
                step = min(int(step), max(0, int(start)))
            if step <= 0:
                return None
            try:
                view._timeline_view_start = int(start + (direction * step))
            except Exception:
                return None
            try:
                sync_fn = getattr(view, "_timeline_sync_range_controls", None)
                if callable(sync_fn):
                    sync_fn(keep_current_visible=False, refresh_key_markers=False)
            except Exception:
                return None
            try:
                lo2 = int(slider.minimum())
                hi2 = int(slider.maximum())
            except Exception:
                lo2 = lo
                hi2 = hi
            if direction > 0:
                return max(int(lo2), min(int(hi2), int(target) + int(step)))
            return max(int(lo2), min(int(hi2), int(target) - int(step)))

        def eventFilter(self, obj, ev):
            slider = self._slider
            if slider is None or obj is not slider:
                return False
            try:
                if ev.type() != QtCore.QEvent.MouseMove:
                    return False
            except Exception:
                return False
            active = bool(getattr(slider, "_jump_drag_active", False))
            try:
                active = active or bool(slider.isSliderDown())
            except Exception:
                pass
            if not bool(active):
                return False
            if not bool(self._left_button_down(ev)):
                return False
            x_pos = self._event_x(ev)
            if x_pos is None:
                return False
            target = self._pixel_x_to_value(int(x_pos))
            if target is None:
                return False
            new_target = self._auto_pan_target(int(x_pos), int(target))
            if new_target is None:
                return False
            try:
                slider.setSliderPosition(int(new_target))
            except Exception:
                pass
            try:
                slider.setValue(int(new_target))
            except Exception:
                pass
            return False

    try:
        from echograph.qt_compat import QtWidgets
    except Exception:
        return

    def wrapped(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        try:
            slider = getattr(self, "_timeline_frame_slider", None)
            if slider is not None and getattr(slider, _TIMELINE_EDGE_FILTER_ATTR, None) is None:
                filt = _TimelineEdgePanFilter(slider, self)
                slider.installEventFilter(filt)
                setattr(slider, _TIMELINE_EDGE_FILTER_ATTR, filt)
        except Exception:
            pass
        return result

    try:
        cls._build_timeline_panel = wrapped
        setattr(cls, _TIMELINE_EDGE_PATCH_KEY, True)
    except Exception:
        return


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("scene", SCENE_SPEC)
    _core.register_spec("scene_assembly", SCENE_SPEC)
    _core.register_spec("scene_outliner", SCENE_SPEC)
    _patch_header_debug_button()
    _patch_timeline_frame_slider_edge_pan()
