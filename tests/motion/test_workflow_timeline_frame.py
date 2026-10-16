import ast
import json
import os
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets

from echograph.ui.gl_view import GraphGLView


class WorkflowTimelineFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        path = Path(__file__).resolve().parents[2] / 'echograph_shelf.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        window = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'EchoGraphWindow')
        wanted = {'_workflow_data_for_save', '_inject_timeline_frame_into_workflow_data',
                  '_restore_workflow_timeline_frame', '_restore_workflow_active_scene_deferred'}
        methods = [node for node in window.body
                   if isinstance(node, ast.FunctionDef) and node.name in wanted]
        namespace = {'Any': object, 'Dict': dict, 'QtCore': QtCore}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), 'exec'), namespace)
        cls.methods = {name: namespace[name] for name in wanted}

    def setUp(self):
        self.graph_scene = QtWidgets.QGraphicsScene()
        self.view = GraphGLView(self.graph_scene)
        self.view._build_timeline_panel()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def window_for_view(self, view):
        window = SimpleNamespace(scene=SimpleNamespace(to_dict=lambda: {'settings': {'view_mode': 'split'}}),
                                 gl_view=view)
        window._inject_panel_layout_into_workflow_data = lambda data: None
        window._inject_scene_restore_into_workflow_data = lambda data: None
        for name, method in self.methods.items():
            setattr(window, name, MethodType(method, window))
        return window

    def test_workflow_save_persists_frame_and_restore_applies_it(self):
        view = self.view
        window = self.window_for_view(view)
        view._timeline_frame_spin.setValue(1149)
        saved = json.loads(json.dumps(window._workflow_data_for_save()))
        self.assertEqual(saved['settings']['timeline_frame'], 1149)
        self.assertEqual(saved['settings']['view_mode'], 'split')

        view._timeline_frame_spin.setValue(0)
        view._timeline_total_max = 240
        view._timeline_view_start = 0
        applied = []
        view._timeline_apply_scene_frame = lambda frame: applied.append(
            (frame, view._mgl_timeline_frame_index()))
        window._restore_workflow_timeline_frame(saved['settings']['timeline_frame'])
        self.assertEqual(view._timeline_current_frame(), 1149)
        self.assertEqual(applied, [(1149, 1149)])
        self.assertEqual(view._timeline_view_start + view._timeline_frame_slider.value(), 1149)
        self.assertEqual(window._workflow_data_for_save()['settings']['timeline_frame'], 1149)

    def test_frame_is_restored_again_after_deferred_scene_loading(self):
        window = self.window_for_view(self.view)
        window._current_path = 'workflow.json'
        window._workflow_load_in_progress = False
        window._restore_workflow_timeline_frame(1149)

        def load_scene(_name):
            self.view._timeline_frame_spin.setValue(0)
            return True

        window._restore_workflow_active_scene = load_scene
        window._restore_workflow_active_scene_deferred(
            'Scene', workflow_path='workflow.json', delay_ms=0,
            on_restored=lambda: window._restore_workflow_timeline_frame(1149))
        self.app.processEvents()
        self.assertEqual(self.view._timeline_current_frame(), 1149)
        self.assertEqual(self.view._mgl_timeline_frame_index(), 1149)


if __name__ == '__main__':
    unittest.main()
