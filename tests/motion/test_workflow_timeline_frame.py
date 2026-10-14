import ast
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest


class WorkflowTimelineFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / 'echograph_shelf.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        window = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'EchoGraphWindow')
        wanted = {'_workflow_data_for_save', '_inject_timeline_frame_into_workflow_data',
                  '_restore_workflow_timeline_frame'}
        methods = [node for node in window.body
                   if isinstance(node, ast.FunctionDef) and node.name in wanted]
        namespace = {'Any': object, 'Dict': dict}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), 'exec'), namespace)
        cls.methods = {name: namespace[name] for name in wanted}

    def test_workflow_save_persists_frame_and_restore_applies_it(self):
        view = SimpleNamespace(_timeline_current_frame=lambda: 83)
        window = SimpleNamespace(scene=SimpleNamespace(to_dict=lambda: {'settings': {'view_mode': 'split'}}),
                                 gl_view=view)
        window._inject_panel_layout_into_workflow_data = lambda data: None
        window._inject_scene_restore_into_workflow_data = lambda data: None
        window._inject_timeline_frame_into_workflow_data = MethodType(
            self.methods['_inject_timeline_frame_into_workflow_data'], window)
        window._workflow_data_for_save = MethodType(self.methods['_workflow_data_for_save'], window)
        saved = window._workflow_data_for_save()
        self.assertEqual(saved['settings']['timeline_frame'], 83)
        self.assertEqual(saved['settings']['view_mode'], 'split')

        restored = []
        view._timeline_on_frame_spin_changed = restored.append
        window._restore_workflow_timeline_frame = MethodType(
            self.methods['_restore_workflow_timeline_frame'], window)
        window._restore_workflow_timeline_frame(saved['settings']['timeline_frame'])
        self.assertEqual(restored, [83])


if __name__ == '__main__':
    unittest.main()
