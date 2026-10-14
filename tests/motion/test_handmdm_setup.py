import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import setup_handmdm as setup
from echograph.motion.backends.handmdm import backend
from echograph.motion.backends.priormdm import paths


class HandSetupTests(unittest.TestCase):
    def archive(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for name in ('inference.py', 'requirements.txt', 'src/model/gaussian.py',
                         'motion_stats/bobsl3dt/mean.pt', 'motion_stats/bobsl3dt/std.pt',
                         'text_stats/bobsl3dt/clip/mean.pt', 'text_stats/bobsl3dt/clip/std.pt'):
                archive.writestr(f'HandMDM-{setup.SOURCE_REVISION}/{name}', 'source')
        stream.seek(0)
        return stream

    def test_install_downloads_hand_source_without_git_or_prior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'third_party/HandMDM'
            repo.mkdir(parents=True)
            (repo / 'inference.py').write_text('source')
            with patch.object(setup, 'REPOSITORY', repo), patch.object(setup, 'LOGS', root / 'logs'), \
                 patch.object(setup, 'storage_root', return_value=root), \
                 patch.object(setup, 'urlopen', return_value=self.archive()) as download, \
                 patch.object(setup, 'run', side_effect=AssertionError('Must not invoke Git')), \
                 patch.object(setup, 'install_environment') as install:
                self.assertEqual(setup.main(['--install', '--application']), 2)
                install.assert_called_once()
                download.assert_called_once()
                self.assertTrue((repo / 'motion_stats/bobsl3dt/mean.pt').is_file())
                self.assertFalse((repo.parent / 'priorMDM').exists())
                setup.ensure_source()
                download.assert_called_once()

    def test_source_conflict_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / 'inference.py'
            source.write_text('custom')
            with patch.object(setup, 'REPOSITORY', repo), patch.object(setup, 'urlopen', return_value=self.archive()):
                with self.assertRaises(FileExistsError):
                    setup.ensure_source()
            self.assertEqual(source.read_text(), 'custom')
            self.assertFalse((repo / 'requirements.txt').exists())

    def test_clip_install_does_not_require_git_or_modify_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            original = 'clip @ git+https://github.com/openai/CLIP.git@a1d071733d7111c9c014f024669f959182114e33\n'
            (repo / 'requirements.txt').write_text(original)
            with patch.object(setup, 'REPOSITORY', repo), patch.object(setup, 'LOGS', repo / 'logs'):
                result = setup.inference_requirements().read_text()
            self.assertNotIn('git+', result)
            self.assertIn('114e33.zip', result)
            self.assertEqual((repo / 'requirements.txt').read_text(), original)

    def test_packaged_defaults_and_legacy_workflow_use_writable_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'Program Files/QubitField/App'
            local = root / 'Local'
            with patch.dict(os.environ, {'ProgramFiles': str(root / 'Program Files'),
                                        'LOCALAPPDATA': str(local), 'QUBITMCP_HOME': ''}), \
                 patch.object(paths, 'PROJECT_ROOT', app), patch.object(backend, 'ROOT', app):
                expected = local / 'QubitMCP/third_party/HandMDM'
                self.assertEqual(backend.HandMDMConfig().repository, expected)
                legacy = app / 'third_party/HandMDM'
                config = backend.HandMDMConfig(legacy, legacy / '.venv/Scripts/python.exe', legacy / 'model.ckpt')
                self.assertEqual(config.repository, expected)
                self.assertEqual(config.checkpoint, expected / 'model.ckpt')
                custom = root / 'custom'
                self.assertEqual(backend.HandMDMConfig(repository=custom).repository, custom)
