"""Exercise graph fitting, view gestures and timeline window layout with Qt."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from echograph.ui.gl_view import GraphGLView
from echograph.ui import hotkeys_config
from echograph.ui.timeline_graph_navigation import curve_ticks


class TimelineGraphNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        cache = patch.object(hotkeys_config, '_KEYMAP_CACHE', dict(hotkeys_config.DEFAULT_KEYMAP))
        cache.start()
        self.addCleanup(cache.stop)
        self.scene = QtWidgets.QGraphicsScene()
        self.view = GraphGLView(self.scene)
        self.view.resize(1800, 1000)
        self.view._build_timeline_panel()
        self.panel = self.view._timeline_panel
        self.host = QtWidgets.QWidget()
        self.host.setAttribute(QtCore.Qt.WA_DontShowOnScreen)
        self.host.resize(1800, 1000)
        self.panel.setParent(self.host)
        self.host.show()
        self.panel.show()
        self.view._timeline_enabled = True
        self.view._timeline_mode = 'owner_keys'
        self.view._timeline_in_frame = 0
        self.view._timeline_out_frame = 240
        self.view._timeline_view_start = 40
        self.view._timeline_view_span = 100
        self.view._timeline_keys = {
            50: dict(xyz=[-120., 10000., 0.], rxyz=[0., 0., 0.], axis_mask=[True, True, False, False, False, False]),
            120: dict(xyz=[240., 10000., 0.], rxyz=[0., 0., 0.], axis_mask=[True, True, False, False, False, False]),
        }
        self.view._timeline_curve_axes_filter = {0}
        self.view._timeline_set_curves_mode(True)
        self.view._timeline_update_mode_controls()
        self.view._timeline_sync_range_controls(keep_current_visible=False, refresh_key_markers=True)
        self.view._layout_timeline_panel()
        self.app.processEvents()
        self.canvas = self.view._timeline_curves_canvas

    def tearDown(self):
        self.view.deleteLater()
        self.host.deleteLater()
        self.scene.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def drag(self, widget, button, start, end, modifiers=QtCore.Qt.NoModifier):
        start, end = QtCore.QPointF(start), QtCore.QPointF(end)
        start_global = widget.mapToGlobal(start.toPoint())
        end_global = widget.mapToGlobal(end.toPoint())
        widget.mousePressEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress,start,
            start_global,button,button,modifiers))
        widget.mouseMoveEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseMove,end,
            end_global,QtCore.Qt.NoButton,button,modifiers))
        widget.mouseReleaseEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,
            widget.mapFromGlobal(end_global),end_global,button,QtCore.Qt.NoButton,modifiers))
        self.app.processEvents()

    def test_fit_button_reveals_large_values_and_preserves_keys_and_frames(self):
        before = deepcopy(self.view._timeline_keys)
        self.assertFalse(self.view._timeline_curve_fit_btn.icon().isNull())
        handles = self.view._timeline_default_axis_handles(0,50)
        with patch.object(self.view,'_timeline_save_to_disk') as save:
            self.view._timeline_curve_fit_btn.click()
            save.assert_not_called()
        self.assertLess(self.view._timeline_curve_min,-120.)
        self.assertGreater(self.view._timeline_curve_max,240.)
        self.assertLess(self.view._timeline_curve_max,10000.)
        for frame,value,x,y in self.canvas._axis_points()[0]:
            self.assertTrue(self.canvas._graph_rect().contains(QtCore.QPoint(round(x),round(y))))
        self.assertEqual(self.view._timeline_keys,before)
        self.assertEqual(self.view._timeline_default_axis_handles(0,50),handles)
        self.assertEqual((self.view._timeline_view_start,self.view._timeline_view_span),(40,100))

    def test_first_ruler_dash_always_labels_the_displayed_start_frame(self):
        for start in (0,3,5,14,15,29,145):
            with self.subTest(start=start):
                self.view._timeline_on_display_range_changed(start,start+60)
                self.app.processEvents()
                labels = [label for label in self.view._timeline_tick_labels if label.isVisible()]
                first = labels[0]
                self.assertEqual(first.text(),str(start))
                x = self.view._timeline_tracks_frame.mapFromGlobal(
                    first.mapToGlobal(first.rect().center())).x()
                self.assertAlmostEqual(x,self.view._timeline_slider_to_tracks_x(0),delta=1.)
                self.assertTrue(all(not first.geometry().intersects(label.geometry()) for label in labels[1:]))

    def test_tied_handles_snap_to_vertical_stretch_and_stop_at_the_key_plane(self):
        view = self.view
        view._timeline_curve_selected = {(0,50)}
        view._timeline_curve_min,view._timeline_curve_max = -300.,400.
        with patch.object(view,'_timeline_save_to_disk'):
            handles = self.canvas._selected_handle_points(self.canvas._axis_points())[0]
            target = QtCore.QPointF(handles['key'][0]+2.,handles['key'][1]-25.)
            self.drag(self.canvas,QtCore.Qt.LeftButton,QtCore.QPointF(*handles['out']),target)
            vertical = view._timeline_axis_handles_for_key(0,50)
            self.assertEqual(vertical['out'][0],0.)
            self.assertEqual(vertical['in'][0],0.)
            self.assertAlmostEqual(vertical['in'][1],-vertical['out'][1])
            self.assertGreater(vertical['out'][1],0.)
            handles = self.canvas._selected_handle_points(self.canvas._axis_points())[0]
            target = QtCore.QPointF(handles['key'][0]-12.,handles['key'][1]-45.)
            self.drag(self.canvas,QtCore.Qt.LeftButton,QtCore.QPointF(*handles['out']),target)
            stretched = view._timeline_axis_handles_for_key(0,50)
            self.assertEqual(stretched['out'][0],0.)
            self.assertGreater(stretched['out'][1],vertical['out'][1])
            self.assertAlmostEqual(stretched['in'][1],-stretched['out'][1])
            self.assertTrue(__import__('math').isfinite(view._timeline_eval_axis_curve(0,50.001)))

    def test_handle_stretch_continues_above_and_below_the_visible_graph_range(self):
        view = self.view
        view._timeline_curve_selected = {(0,50)}
        view._timeline_curve_min,view._timeline_curve_max = -300.,400.
        rect = self.canvas._graph_rect()
        for side,y in (('out',rect.top()-250.),('in',rect.bottom()+250.)):
            with self.subTest(side=side),patch.object(view,'_timeline_save_to_disk'):
                view._timeline_set_entry_axis_handles(view._timeline_keys[50],0,50,'tied',(-3.,0.),(3.,0.))
                handles = self.canvas._selected_handle_points(self.canvas._axis_points())[0]
                self.drag(self.canvas,QtCore.Qt.LeftButton,QtCore.QPointF(*handles[side]),
                          QtCore.QPointF(handles['key'][0],y))
                after = view._timeline_axis_handles_for_key(0,50)
                value = view._timeline_keys[50]['xyz'][0]+after[side][1]
                if side == 'out':
                    self.assertGreater(value,view._timeline_curve_max)
                else:
                    self.assertLess(value,view._timeline_curve_min)
                self.assertEqual(after[side][0],0.)
                self.assertAlmostEqual(after['in'][1],-after['out'][1])
                self.assertAlmostEqual(value,self.canvas._y_to_value(y,clamp=False))
        self.assertEqual(self.canvas._y_to_value(rect.top()-250.),view._timeline_curve_max)

    def test_untie_toolbar_creates_pickable_independent_handles_from_straight_and_keeps_opposite_curve(self):
        view = self.view
        view._timeline_keys[85] = dict(xyz=[20.,0.,0.],axis_mask=[True,False,False,False,False,False])
        view._timeline_curve_selected = {(0,85)}
        view._timeline_curve_min,view._timeline_curve_max = -300.,400.
        view._timeline_update_handle_mode_buttons()
        with patch.object(view,'_timeline_save_to_disk'):
            view._timeline_handle_straight_btn.click()
            view._timeline_handle_untied_btn.click()
            before = view._timeline_axis_handles_for_key(0,85)
            self.assertEqual(before['mode'],'untied')
            self.assertLess(before['in'][0],0.)
            self.assertGreater(before['out'][0],0.)
            curve_before = view._timeline_eval_axis_curve(0,75.)
            for side,other,dx in (('out','in',14.),('in','out',-14.)):
                handles = self.canvas._selected_handle_points(self.canvas._axis_points())[0]
                point = QtCore.QPointF(*handles[side])
                previous = view._timeline_axis_handles_for_key(0,85)
                self.drag(self.canvas,QtCore.Qt.LeftButton,point,point+QtCore.QPointF(dx,-20.))
                after = view._timeline_axis_handles_for_key(0,85)
                self.assertEqual(after['mode'],'untied')
                self.assertEqual(after[other],previous[other])
                self.assertNotEqual(after[side],previous[side])
                if side == 'out':
                    self.assertAlmostEqual(view._timeline_eval_axis_curve(0,75.),curve_before)
            view._timeline_handle_tied_btn.click()
            self.assertEqual(view._timeline_selected_handle_mode(),'tied')

    def test_neighboring_key_handles_can_overlap_and_extend_without_a_length_cap(self):
        view = self.view
        view._timeline_keys = {
            frame:dict(xyz=[value,0.,0.],axis_mask=[True,False,False,False,False,False])
            for frame,value in ((50,0.),(51,1.))}
        view._timeline_on_display_range_changed(48,53)
        view._timeline_curve_min,view._timeline_curve_max = -2.,3.
        view._timeline_curve_selected = {(0,50),(0,51)}
        for frame in (50,51):
            view._timeline_set_entry_axis_handles(view._timeline_keys[frame],0,frame,'untied',
                (-.2,-.2),(.2,.2),weighted=['in','out'])
        self.app.processEvents()
        for frame,side,dx,dy in ((50,'out',.8,.4),(51,'in',-.8,-.4)):
            with self.subTest(frame=frame),patch.object(view,'_timeline_save_to_disk'):
                handles = self.canvas._selected_handle_points(self.canvas._axis_points())
                handle = next(item for item in handles if item['frame'] == frame)
                x = view._timeline_local_frame_to_tracks_x_float(frame-view._timeline_view_start+dx,clamp=False)
                y = self.canvas._value_to_y(view._timeline_keys[frame]['xyz'][0]+dy)
                self.drag(self.canvas,QtCore.Qt.LeftButton,QtCore.QPointF(*handle[side]),QtCore.QPointF(x,y))
                actual = view._timeline_axis_handles_for_key(0,frame)[side]
                self.assertAlmostEqual(actual[0],dx,delta=1.5/view._timeline_tracks_pixels_per_frame(frame-view._timeline_view_start))
                self.assertAlmostEqual(actual[1],dy)
        handles = self.canvas._selected_handle_points(self.canvas._axis_points())
        self.assertGreater(handles[0]['out'][0],handles[1]['in'][0])
        view._timeline_set_axis_handle_value(0,50,'out',25.,1500.)
        view._timeline_set_axis_handle_value(0,51,'in',-30.,-2000.)
        self.assertEqual(view._timeline_axis_handles_for_key(0,50)['out'],(25.,1500.))
        self.assertEqual(view._timeline_axis_handles_for_key(0,51)['in'],(-30.,-2000.))
        payload = view._timeline_owner_keys_payload_rows(view._timeline_keys)
        restored = view._timeline_parse_keys_rows(payload)
        self.assertEqual(restored[50]['curve_handles']['0']['out'],[25.,1500.])
        self.assertEqual(restored[51]['curve_handles']['0']['in'],[-30.,-2000.])

    def test_vertical_handle_weights_survive_curve_key_copy_payload_and_paste_parsing(self):
        self.view._timeline_set_axis_handle_value(0,50,'out',0.,80.)
        original = self.view._timeline_keys[50]['curve_handles']['0']
        payload = self.view._timeline_owner_keys_payload_rows(self.view._timeline_keys)
        restored = self.view._timeline_parse_keys_rows(payload)
        self.assertEqual(restored[50]['curve_handles']['0'],original)

    def test_dense_graph_paint_and_fit_do_not_scan_all_keys_for_each_tangent(self):
        count = 180
        self.view._timeline_keys = {frame:dict(xyz=[frame*.01]*3,rxyz=[frame*.02]*3,axis_mask=[True]*6)
                                    for frame in range(count)}
        self.view._timeline_curve_axes_filter = set(range(6))
        self.view._timeline_on_display_range_changed(0,count)
        self.app.processEvents()
        for operation in (self.canvas.grab,self.view._timeline_fit_curve_values):
            with self.subTest(operation=operation.__name__), patch.object(
                    self.view,'_timeline_axis_is_keyed',wraps=self.view._timeline_axis_is_keyed) as keyed:
                operation()
                self.assertLessEqual(keyed.call_count,18*count)
                self.assertIsNone(self.view._timeline_curve_read_cache)

    def test_curve_read_indexes_preserve_tangents_and_refresh_after_edits(self):
        expected = {frame:self.view._timeline_axis_handles_for_key(0,frame) for frame in (50,120)}
        with self.view._timeline_curve_read_scope():
            for frame,handles in expected.items():
                self.assertEqual(self.view._timeline_axis_handles_for_key(0,frame),handles)
        self.view._timeline_keys[75] = dict(xyz=[100.,0.,0.],axis_mask=[True,False,False,False,False,False])
        self.view._timeline_keys[50]['xyz'][0] = 400.
        changed = self.view._timeline_axis_handles_for_key(0,50)
        self.assertNotEqual(changed,expected[50])
        with self.view._timeline_curve_read_scope():
            self.assertEqual(self.view._timeline_axis_key_points(0),[(50,400.),(75,100.),(120,240.)])
            self.assertEqual(self.view._timeline_axis_handles_for_key(0,50),changed)
        with self.assertRaisesRegex(RuntimeError,'abort read'):
            with self.view._timeline_curve_read_scope():
                raise RuntimeError('abort read')
        self.assertIsNone(self.view._timeline_curve_read_cache)

    def test_right_drag_scales_view_ranges_and_updates_bottom_handles_without_editing_keys(self):
        before = deepcopy(self.view._timeline_keys)
        center = self.canvas.rect().center()
        with patch.object(self.view,'_timeline_save_to_disk') as save, \
                patch.object(self.view,'_timeline_range_save_to_disk') as save_range:
            self.drag(self.canvas,QtCore.Qt.RightButton,center,center+QtCore.QPoint(0,80))
            self.assertGreater(self.view._timeline_curve_max-self.view._timeline_curve_min,10.)
            self.assertEqual((self.view._timeline_view_start,self.view._timeline_view_span),(40,100))
            save_range.assert_not_called()
            self.drag(self.canvas,QtCore.Qt.RightButton,center,center+QtCore.QPoint(80,0))
            self.assertLess(self.view._timeline_view_span,100)
            controls = self.view._timeline_range_controls
            self.assertEqual(controls.bar._range,(self.view._timeline_view_start,
                self.view._timeline_view_start+self.view._timeline_view_span))
            save_range.assert_called_once()
            contracted = self.view._timeline_view_span
            self.drag(self.canvas,QtCore.Qt.RightButton,center,center-QtCore.QPoint(80,0))
            self.assertGreater(self.view._timeline_view_span,contracted)
            expanded = self.view._timeline_curve_max-self.view._timeline_curve_min
            self.drag(self.canvas,QtCore.Qt.RightButton,center,center-QtCore.QPoint(0,80))
            self.assertLess(self.view._timeline_curve_max-self.view._timeline_curve_min,expanded)
            self.assertEqual(save_range.call_count,2)
            save.assert_not_called()
        self.assertEqual(self.view._timeline_keys,before)
        self.assertEqual((self.view._timeline_in_frame,self.view._timeline_out_frame),(0,240))

    def test_alt_right_drag_scales_both_ranges_without_editing_keys(self):
        before = deepcopy(self.view._timeline_keys)
        center = self.canvas.rect().center()
        with patch.object(self.view,'_timeline_save_to_disk') as save, \
                patch.object(self.view,'_timeline_range_save_to_disk') as save_range:
            self.drag(self.canvas,QtCore.Qt.RightButton,center,center+QtCore.QPoint(80,80),
                      QtCore.Qt.AltModifier)
            contracted_frames = self.view._timeline_view_span
            expanded_values = self.view._timeline_curve_max-self.view._timeline_curve_min
            self.assertLess(contracted_frames,100)
            self.assertGreater(expanded_values,10.)
            self.assertEqual(self.view._timeline_range_controls.bar._range,
                             (self.view._timeline_view_start,self.view._timeline_view_start+contracted_frames))
            self.drag(self.canvas,QtCore.Qt.RightButton,center,center-QtCore.QPoint(80,80),
                      QtCore.Qt.AltModifier)
            self.assertGreater(self.view._timeline_view_span,contracted_frames)
            self.assertLess(self.view._timeline_curve_max-self.view._timeline_curve_min,expanded_values)
            self.assertEqual(save_range.call_count,2)
            save.assert_not_called()
        self.assertEqual(self.view._timeline_keys,before)

    def test_vertical_bar_can_expand_each_end_and_pan(self):
        bar = self.view._timeline_value_range_bar
        top,bottom = bar.handle_positions()
        self.drag(bar,QtCore.Qt.LeftButton,QtCore.QPoint(11,round(top)),QtCore.QPoint(11,round(top)-10))
        self.assertGreater(self.view._timeline_curve_max,5.)
        self.assertEqual(self.view._timeline_curve_min,-5.)
        top,bottom = bar.handle_positions()
        self.drag(bar,QtCore.Qt.LeftButton,QtCore.QPoint(11,round(bottom)),QtCore.QPoint(11,round(bottom)+10))
        self.assertLess(self.view._timeline_curve_min,-5.)
        low,high = self.view._timeline_curve_min,self.view._timeline_curve_max
        top,bottom = bar.handle_positions()
        center = QtCore.QPoint(11,round((top+bottom)/2))
        self.drag(bar,QtCore.Qt.LeftButton,center,center+QtCore.QPoint(0,10))
        self.assertAlmostEqual(self.view._timeline_curve_max-self.view._timeline_curve_min,high-low)
        self.assertLess(self.view._timeline_curve_min,low)

    def test_alt_middle_drag_grabs_the_graph_in_both_axes_without_changing_scale_or_keys(self):
        before = deepcopy(self.view._timeline_keys)
        self.view._timeline_fit_curve_values()
        self.view._timeline_curve_selected = {(0,50)}
        low, high = self.view._timeline_curve_min,self.view._timeline_curve_max
        selected = self.view._timeline_curve_selected.copy()
        _,_,x,y = self.canvas._axis_points()[0][0]
        with patch.object(self.view,'_timeline_save_to_disk') as save, \
                patch.object(self.view,'_timeline_range_save_to_disk') as save_range:
            self.drag(self.canvas,QtCore.Qt.MiddleButton,QtCore.QPointF(x,y),
                      QtCore.QPointF(x-90,y+50),QtCore.Qt.AltModifier)
            save.assert_not_called()
            save_range.assert_called_once()
        self.assertGreater(self.view._timeline_view_start,40)
        self.assertEqual(self.view._timeline_view_span,100)
        self.assertAlmostEqual(self.view._timeline_curve_max-self.view._timeline_curve_min,high-low)
        self.assertGreater(self.view._timeline_curve_min,low)
        _,_,moved_x,moved_y = self.canvas._axis_points()[0][0]
        self.assertAlmostEqual(moved_x-x,-90,delta=self.view._timeline_tracks_pixels_per_frame(0))
        self.assertAlmostEqual(moved_y-y,50,delta=1.)
        self.assertEqual(self.view._timeline_curve_selected,selected)
        self.assertEqual(self.view._timeline_keys,before)
        self.assertIsNone(self.canvas._range_drag)
        self.assertNotEqual(self.canvas.cursor().shape(),QtCore.Qt.ClosedHandCursor)
        self.assertEqual(self.view._timeline_range_controls.bar._range,
                         (self.view._timeline_view_start,self.view._timeline_view_start+100))

    def test_pan_clamps_to_full_time_bounds_and_can_return_to_the_original_view(self):
        point = QtCore.QPointF(self.canvas.rect().center())
        def event(kind, position, button, buttons, modifiers):
            return QtGui.QMouseEvent(kind,position,self.canvas.mapToGlobal(position.toPoint()),
                                    button,buttons,modifiers)
        self.canvas.mousePressEvent(event(QtCore.QEvent.MouseButtonPress,point,
            QtCore.Qt.MiddleButton,QtCore.Qt.MiddleButton,QtCore.Qt.AltModifier))
        with patch.object(self.view,'_timeline_range_save_to_disk') as save_range:
            for dx, expected in ((5000,0),(-5000,140),(0,40)):
                self.canvas.mouseMoveEvent(event(QtCore.QEvent.MouseMove,point+QtCore.QPointF(dx,80),
                    QtCore.Qt.NoButton,QtCore.Qt.MiddleButton,QtCore.Qt.NoModifier))
                self.assertEqual((self.view._timeline_view_start,self.view._timeline_view_span),(expected,100))
            self.canvas.mouseReleaseEvent(event(QtCore.QEvent.MouseButtonRelease,point,
                QtCore.Qt.MiddleButton,QtCore.Qt.NoButton,QtCore.Qt.NoModifier))
            save_range.assert_called_once()
        self.assertEqual((self.view._timeline_curve_min,self.view._timeline_curve_max),(-5.,5.))

    def test_graph_shortcuts_follow_remapping_in_the_hotkey_editor(self):
        hotkeys_config._KEYMAP_CACHE.update(timeline_graph_pan='Ctrl+LeftClick',
            timeline_graph_scale='Shift+RightClick',timeline_graph_frame_selected='Shift+F6')
        point = self.canvas.rect().center()
        self.drag(self.canvas,QtCore.Qt.MiddleButton,point,point+QtCore.QPoint(0,40),QtCore.Qt.AltModifier)
        self.assertEqual((self.view._timeline_curve_min,self.view._timeline_curve_max),(-5.,5.))
        self.drag(self.canvas,QtCore.Qt.LeftButton,point,point+QtCore.QPoint(0,40),QtCore.Qt.ControlModifier)
        self.assertGreater(self.view._timeline_curve_min,-5.)
        self.assertEqual(self.view._timeline_curve_max-self.view._timeline_curve_min,10.)
        self.drag(self.canvas,QtCore.Qt.RightButton,point,point+QtCore.QPoint(0,40),QtCore.Qt.ShiftModifier)
        self.assertGreater(self.view._timeline_curve_max-self.view._timeline_curve_min,10.)
        span = self.view._timeline_curve_max-self.view._timeline_curve_min
        self.drag(self.canvas,QtCore.Qt.RightButton,point,point+QtCore.QPoint(0,40),
                  QtCore.Qt.ShiftModifier | QtCore.Qt.AltModifier)
        self.assertGreater(self.view._timeline_curve_max-self.view._timeline_curve_min,span)
        self.view._timeline_curve_selected = {(0,50)}
        before = self.view._timeline_curve_min,self.view._timeline_curve_max
        QtTest.QTest.keyClick(self.canvas,QtCore.Qt.Key_F)
        self.assertEqual((self.view._timeline_curve_min,self.view._timeline_curve_max),before)
        with patch.object(self.view,'_timeline_range_save_to_disk'):
            QtTest.QTest.keyClick(self.canvas,QtCore.Qt.Key_F6,QtCore.Qt.ShiftModifier)
        self.assertEqual((self.view._timeline_view_start,self.view._timeline_view_span),(45,10))
        self.assertEqual((self.view._timeline_curve_min,self.view._timeline_curve_max),(-121.,-119.))

    def test_start_end_labels_are_visible_above_keys_in_curves_and_rows_without_changing_markers(self):
        from echograph.ui.gl_view_timeline_model import TimelineCorrectionKeys
        spans = [dict(start=dict(id='start'),end=dict(id='end'))]
        self.view._timeline_keys[50].update(ik_key='start',ik_axis_mask=[True,False,False,False,False,False])
        self.view._timeline_keys[120].update(ik_key='end',ik_axis_mask=[True,False,False,False,False,False])
        self.view._timeline_set_curve_range(-200.,300.)
        def render(widget):
            image = QtGui.QImage(widget.size(),QtGui.QImage.Format_ARGB32)
            image.fill(QtCore.Qt.transparent)
            widget.render(image)
            return image
        with patch.object(self.view,'_timeline_correction_session',return_value=(SimpleNamespace(ik_spans=spans),'wrist')), \
                patch.object(self.view,'_timeline_paint_ik_ranges'):
            with patch.object(self.view,'_timeline_ik_boundary_labels',return_value={}):
                before = render(self.canvas)
            after = render(self.canvas)
            for _frame,_value,x,y in self.canvas._axis_points()[0]:
                rect = QtCore.QRect(round(x)-6,round(y)-18,12,14)
                self.assertNotEqual(before.copy(rect),after.copy(rect))
                marker = QtCore.QRect(round(x)-4,round(y)-4,8,8)
                self.assertEqual(before.copy(marker),after.copy(marker))
            # The correction's row layer batches all markers into one image.
            # Its labels must appear without adding per-key child widgets.
            row_view = SimpleNamespace(_timeline_curve_selected=set(),
                _timeline_paint_ik_ranges=lambda painter:None,
                _timeline_visible_row_key_points=lambda:[(0,50,50.,11.,0.),(0,120,120.,11.,0.)],
                _timeline_key_is_ik=self.view._timeline_key_is_ik,
                _timeline_ik_boundary_label=self.view._timeline_ik_boundary_label,
                _timeline_ik_boundary_labels=self.view._timeline_ik_boundary_labels)
            layer = TimelineCorrectionKeys(row_view,None)
            layer.resize(180,40)
            self.addCleanup(layer.deleteLater)
            # Paint directly so each revision bypasses the widget's image cache.
            def draw_rows():
                image = QtGui.QImage(layer.size(),QtGui.QImage.Format_ARGB32)
                image.fill(QtCore.Qt.transparent)
                painter = QtGui.QPainter(image)
                layer._paint_keys(painter)
                painter.end()
                return image
            original_labels = row_view._timeline_ik_boundary_labels
            row_view._timeline_ik_boundary_labels = lambda:{}
            before = draw_rows()
            row_view._timeline_ik_boundary_labels = original_labels
            after = draw_rows()
            for x in (50,120):
                self.assertNotEqual(before.copy(x-6,0,12,7),after.copy(x-6,0,12,7))
                self.assertEqual(before.copy(x-4,7,8,8),after.copy(x-4,7,8,8))
            self.assertFalse(layer.findChildren(QtWidgets.QWidget))

    def test_right_click_opens_key_actions_but_drag_and_alt_click_do_not(self):
        point = self.canvas.rect().center()
        with patch.object(self.view,'_timeline_show_key_context_menu') as menu, \
                patch.object(self.view,'_timeline_range_save_to_disk'):
            self.drag(self.canvas,QtCore.Qt.RightButton,point,point)
            menu.assert_called_once()
            menu.reset_mock()
            self.drag(self.canvas,QtCore.Qt.RightButton,point,point+QtCore.QPoint(40,40))
            menu.assert_not_called()
            self.drag(self.canvas,QtCore.Qt.RightButton,point,point,QtCore.Qt.AltModifier)
            menu.assert_not_called()
        self.assertIsNone(self.canvas._range_drag)

    def test_join_ik_shortcut_is_remappable_and_never_falls_through_to_delete(self):
        with patch.object(self.view,'_timeline_join_selected_ik_ranges',return_value=False) as join, \
                patch.object(self.view,'_timeline_delete_selected_keys') as delete:
            QtTest.QTest.keyClick(self.canvas,QtCore.Qt.Key_J,QtCore.Qt.ControlModifier)
            join.assert_called_once()
            delete.assert_not_called()
            hotkeys_config._KEYMAP_CACHE['timeline_join_ik_ranges'] = 'Shift+F8'
            QtTest.QTest.keyClick(self.canvas,QtCore.Qt.Key_J,QtCore.Qt.ControlModifier)
            self.assertEqual(join.call_count,1)
            QtTest.QTest.keyClick(self.canvas,QtCore.Qt.Key_F8,QtCore.Qt.ShiftModifier)
            self.assertEqual(join.call_count,2)
            field = QtWidgets.QLineEdit(self.panel)
            field.show()
            self.app.setActiveWindow(self.host)
            field.setFocus()
            self.assertIs(self.app.focusWidget(),field)
            QtTest.QTest.keyClick(field,QtCore.Qt.Key_F8,QtCore.Qt.ShiftModifier)
            self.assertEqual(join.call_count,2)
        self.assertIn('timeline_join_ik_ranges',hotkeys_config.ACTION_LABELS)
        self.assertIn('intermediate IK keys',hotkeys_config.ACTION_DESCRIPTIONS['timeline_join_ik_ranges'])

    def test_join_ik_menu_requires_two_compatible_inner_keys_and_preserves_selection(self):
        spans = [dict(joint='left_wrist',start=dict(id='a',time=1.),end=dict(id='b',time=2.)),
                 dict(joint='left_wrist',start=dict(id='c',time=4.),end=dict(id='d',time=5.))]
        session = SimpleNamespace(ik_spans=spans)
        self.view._timeline_keys[50].update(ik_key='b',ik_axis_mask=[True]*6)
        self.view._timeline_keys[120].update(ik_key='c',ik_axis_mask=[True]*6)
        self.view._timeline_curve_selected = {(0,50),(0,120)}
        menu = QtWidgets.QMenu(self.canvas)
        with patch.object(self.view,'_timeline_correction_session',return_value=(session,'left_wrist')), \
                patch('echograph.ui.gl_view_timeline_model.QtWidgets.QMenu',return_value=menu), \
                patch.object(menu,'exec'), \
                patch.object(self.view,'_timeline_join_selected_ik_ranges') as join:
            self.assertTrue(self.view._timeline_show_key_context_menu(self.canvas,QtCore.QPoint()))
            action = menu.actions()[0]
            self.assertEqual(action.text(),'Join IK ranges')
            self.assertTrue(action.isEnabled())
            self.assertEqual(action.shortcut().toString(),'Ctrl+J')
            action.trigger()
            join.assert_called_once()
        self.assertEqual(self.view._timeline_curve_selected,{(0,50),(0,120)})
        menu = QtWidgets.QMenu(self.canvas)
        self.view._timeline_curve_selected = {(0,50)}
        with patch.object(self.view,'_timeline_correction_session',return_value=(session,'left_wrist')), \
                patch('echograph.ui.gl_view_timeline_model.QtWidgets.QMenu',return_value=menu), \
                patch.object(menu,'exec'):
            self.view._timeline_show_key_context_menu(self.canvas,QtCore.QPoint())
            self.assertFalse(menu.actions()[0].isEnabled())
            self.assertIn('Select an End IK key',menu.actions()[1].defaultWidget().text())

    def test_f_frames_only_selected_visible_points_and_updates_the_range_controls(self):
        self.view._timeline_keys[220] = dict(xyz=[900000.,0.,0.],axis_mask=[True,False,False,False,False,False])
        before = deepcopy(self.view._timeline_keys)
        self.view._timeline_curve_selected = {(0,50),(0,120),(1,50)}
        with patch.object(self.view,'_timeline_save_to_disk') as save, \
                patch.object(self.view,'_timeline_range_save_to_disk') as save_range:
            QtTest.QTest.keyClick(self.canvas,QtCore.Qt.Key_F)
            save.assert_not_called()
            save_range.assert_called_once()
        self.assertEqual((self.view._timeline_view_start,self.view._timeline_view_span),(43,84))
        self.assertEqual(self.view._timeline_range_controls.bar._range,(43,127))
        self.assertGreater(self.view._timeline_curve_max,240.)
        self.assertLess(self.view._timeline_curve_max,10000.)
        self.assertEqual(self.view._timeline_keys,before)
        for frame,value,x,y in self.canvas._axis_points()[0]:
            self.assertTrue(self.canvas._graph_rect().contains(QtCore.QPoint(round(x),round(y))))

    def test_f_without_selection_frames_all_curve_keys_including_keys_outside_the_view(self):
        self.view._timeline_keys[220] = dict(xyz=[900000.,0.,0.],axis_mask=[True,False,False,False,False,False])
        self.view._timeline_keys[50]['xyz'][1] = 1e12  # Hidden Py must not determine the fit.
        self.view._timeline_curve_selected = set()
        before = deepcopy(self.view._timeline_keys)
        with patch.object(self.view,'_timeline_save_to_disk') as save, \
                patch.object(self.view,'_timeline_range_save_to_disk') as save_range:
            QtTest.QTest.keyClick(self.canvas,QtCore.Qt.Key_F)
            save.assert_not_called()
            save_range.assert_called_once()
        self.assertEqual((self.view._timeline_view_start,self.view._timeline_view_span),(33,204))
        self.assertEqual(self.view._timeline_range_controls.bar._range,(33,237))
        self.assertLess(self.view._timeline_curve_min,-120.)
        self.assertGreater(self.view._timeline_curve_max,900000.)
        self.assertLess(self.view._timeline_curve_max,1e12)
        self.assertFalse(self.view._timeline_curve_selected)
        self.assertEqual(self.view._timeline_keys,before)
        points = self.canvas._axis_points()[0]
        self.assertEqual({point[0] for point in points},{50,120,220})
        for frame,value,x,y in points:
            self.assertTrue(self.canvas._graph_rect().contains(QtCore.QPoint(round(x),round(y))))

    def test_right_drag_can_return_to_its_original_ranges_in_one_gesture(self):
        point = QtCore.QPointF(self.canvas.rect().center())
        global_point = self.canvas.mapToGlobal(point.toPoint())
        with patch.object(self.view,'_timeline_range_save_to_disk'):
            self.canvas.mousePressEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress,point,
                global_point,QtCore.Qt.RightButton,QtCore.Qt.RightButton,QtCore.Qt.NoModifier))
            for moved in (point+QtCore.QPointF(80,80),point):
                self.canvas.mouseMoveEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseMove,moved,
                    self.canvas.mapToGlobal(moved.toPoint()),QtCore.Qt.NoButton,
                    QtCore.Qt.RightButton,QtCore.Qt.NoModifier))
            self.canvas.mouseReleaseEvent(QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease,point,
                global_point,QtCore.Qt.RightButton,QtCore.Qt.NoButton,QtCore.Qt.NoModifier))
        self.assertEqual((self.view._timeline_view_start,self.view._timeline_view_span),(40,100))
        self.assertAlmostEqual(self.view._timeline_curve_min,-5.)
        self.assertAlmostEqual(self.view._timeline_curve_max,5.)

    def test_fitted_keys_at_both_time_boundaries_are_inside_the_graph(self):
        self.view._timeline_on_display_range_changed(50,120)
        self.view._timeline_fit_curve_values()
        self.app.processEvents()
        for frame,value,x,y in self.canvas._axis_points()[0]:
            self.assertTrue(self.canvas._graph_rect().contains(QtCore.QPoint(round(x),round(y))))

    def test_ruler_labels_align_with_the_playhead_keys_and_sequences_in_every_mode(self):
        self.view._timeline_keys = {frame:dict(xyz=[0.,0.,0.],axis_mask=[True,False,False,False,False,False])
                                    for frame in (0,15,120)}
        block = dict(owner='Clip',label='Clip',clip_start_frame=0,clip_end_frame=120)
        self.view._timeline_composition_blocks = [block]
        for detached in (False,True):
            self.view._timeline_set_detached(detached)
            if detached:
                self.view._timeline_window.resize(1300,520)
            for mode in ('curves','rows','composition','curves'):
                with self.subTest(detached=detached,mode=mode):
                    self.view._timeline_mode = 'composition' if mode == 'composition' else 'owner_keys'
                    self.view._timeline_set_curves_mode(mode == 'curves')
                    self.view._timeline_update_mode_controls()
                    self.view._timeline_on_display_range_changed(0,120)
                    self.view._layout_timeline_panel()
                    self.app.processEvents()
                    slider = self.view._timeline_frame_slider
                    tracks = self.view._timeline_tracks_frame
                    labels = {int(label.text()):label for label in self.view._timeline_tick_labels
                              if label.isVisible()}
                    for frame in (0,15,120):
                        label = labels[frame]
                        ruler_x = tracks.mapFromGlobal(label.mapToGlobal(label.rect().center())).x()
                        frame_x = self.view._timeline_slider_to_tracks_x(frame)
                        self.assertAlmostEqual(ruler_x,frame_x,delta=1.)
                        slider_x = self.view._timeline_slider_value_to_x(frame,slider=slider)
                        self.assertEqual(self.view._timeline_tracks_x_to_slider_value(frame_x),frame)
                        self.assertEqual(slider._pixel_x_to_value(slider_x),frame)
                    zero_x = self.view._timeline_slider_to_tracks_x(0)
                    if mode == 'curves':
                        self.assertGreaterEqual(zero_x,self.canvas._graph_rect().left())
                        points = self.canvas._axis_points()[0]
                        self.assertEqual(points[0][2],zero_x)
                    elif mode == 'rows':
                        self.assertLess(zero_x,20)
                        points = self.view._timeline_visible_row_key_points()
                        self.assertEqual(points[0][2],zero_x)
                    else:
                        self.assertLess(zero_x,20)
                        rect = self.view._timeline_composition_canvas._block_rect(block,0)
                        self.assertEqual(rect.left(),zero_x)

    def test_enlarging_panel_only_grows_the_graph_and_detaching_restores_the_same_panel(self):
        buttons = (self.view._timeline_play_btn,self.view._timeline_curves_btn,self.view._timeline_curve_fit_btn)
        self.assertFalse(self.view._timeline_detach_btn.icon().isNull())
        self.assertEqual(self.view._timeline_detach_btn.iconSize(),QtCore.QSize(24,24))
        self.assertEqual(self.view._timeline_detach_btn.text(),'')
        before_buttons = [button.geometry() for button in buttons]
        before_labels = [button.geometry() for button in self.view._timeline_axis_labels]
        initial_height, graph_height = self.panel.height(), self.canvas.height()
        self.drag(self.panel,QtCore.Qt.LeftButton,QtCore.QPoint(20,3),QtCore.QPoint(20,-137))
        self.app.processEvents()
        self.assertGreaterEqual(self.canvas.height(),graph_height+130)
        self.assertEqual([button.geometry() for button in buttons],before_buttons)
        self.assertEqual([button.geometry() for button in self.view._timeline_axis_labels],before_labels)
        before = deepcopy(self.view._timeline_keys)
        self.view._timeline_detach_btn.click()
        self.app.processEvents()
        window = self.view._timeline_window
        self.assertTrue(window.isVisible())
        self.assertTrue(self.view._timeline_detached)
        self.assertEqual(self.view._timeline_detach_btn.text(),'')
        self.assertIs(self.panel.window(),window)
        self.assertEqual(self.view._bottom_overlay_height(),0.)
        labels = [button.geometry() for button in self.view._timeline_axis_labels]
        height = self.canvas.height()
        window.resize(window.width(),window.height()+160)
        self.app.processEvents()
        self.assertGreaterEqual(self.canvas.height(),height+140)
        self.assertEqual([button.geometry() for button in self.view._timeline_axis_labels],labels)
        window.close()
        self.app.processEvents()
        self.assertFalse(self.view._timeline_detached)
        self.assertIs(self.view._timeline_panel,self.panel)
        self.assertIs(self.panel.parentWidget(),self.view)
        self.assertEqual(self.view._timeline_keys,before)

    def test_rig_limiter_panel_moves_above_the_timeline_without_resyncing_joint_controls(self):
        from echograph.motion.rig_preview import build_guide,preview_offsets
        from echograph.ui.priormdm_rig_panel import RigPreviewPanel
        _, guide = build_guide(preview_offsets('standardman'))
        guide.update(profile='standardman',collisions=True,limits=True)
        rig = RigPreviewPanel(self.view)
        rig.setParent(self.host)
        rig.sync(dict(owner='Rig',selected='left_ankle',rig_context={},rig_guide=guide,
                      rig_viewport=(0,0,self.view.width(),self.view.height())),'Ankle')
        self.view._priormdm_rig_panel = rig
        rig.place()
        rig.show()
        self.app.processEvents()
        initial_top = rig.y()
        fields = rig.fields.copy()
        with patch.object(rig,'sync') as sync:
            self.view._timeline_resize_panel(self.panel.height()+150)
            self.app.processEvents()
            self.assertLess(rig.y(),initial_top)
            self.assertLess(rig.geometry().bottom(),self.panel.y())
            self.view._timeline_resize_panel(700)
            self.app.processEvents()
            self.assertLess(rig.geometry().bottom(),self.panel.y())
            self.assertGreater(rig.scroll.verticalScrollBar().maximum(),0)
            self.view._timeline_set_detached(True)
            self.app.processEvents()
            self.assertGreater(rig.geometry().bottom(),700)
            self.view._timeline_set_detached(False)
            # This fixture hosts visible widgets outside the hidden GL view.
            self.panel.setParent(self.host)
            self.panel.show()
            self.view._layout_timeline_panel()
            self.app.processEvents()
            self.assertLess(rig.geometry().bottom(),self.panel.y())
            self.view.set_timeline_visible(False)
            self.app.processEvents()
            self.assertGreater(rig.geometry().bottom(),700)
            self.assertEqual(rig.scroll.verticalScrollBar().maximum(),0)
            sync.assert_not_called()
        self.assertEqual(rig.fields,fields)

    def test_detached_panel_keeps_timeline_keyboard_controls_and_can_hide_and_reopen(self):
        self.view._timeline_set_detached(True)
        self.app.processEvents()
        self.canvas.setFocus()
        with patch.object(self.view,'_timeline_delete_selected_keys',return_value=True) as delete:
            QtTest.QTest.keyClick(self.canvas,QtCore.Qt.Key_Delete)
            delete.assert_called_once()
        self.view.set_timeline_visible(False)
        self.assertFalse(self.view._timeline_window.isVisible())
        self.view.set_timeline_visible(True)
        self.assertTrue(self.view._timeline_window.isVisible())
        self.assertIs(self.view._timeline_panel,self.panel)

    def test_grid_work_is_bounded_for_large_and_small_ranges(self):
        for low,high in ((-1e9,1e9),(-.0001,.0001),(1000.,1001.)):
            ticks = curve_ticks(low,high,300)
            self.assertTrue(1 <= len(ticks) <= 24)
            self.assertTrue(all(low <= value <= high for value in ticks))


if __name__ == '__main__':
    unittest.main()
