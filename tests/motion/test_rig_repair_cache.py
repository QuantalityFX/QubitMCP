from pathlib import Path
import tempfile
import unittest

from echograph.motion import rig_repair_cache as cache


class RepairFileTests(unittest.TestCase):
    def test_source_settings_and_output_content_must_match(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'motion.bvh'
            source.write_bytes(b'original animation')
            record = cache.save_repair(source, 'settings-one', {}, lambda p: p.write_bytes(b'repaired'))
            output = cache.repair_path(source, record)
            self.assertEqual(cache.find_repair(source, 'settings-one'), record)
            self.assertIsNone(cache.find_repair(source, 'settings-two'))
            self.assertEqual(cache.generated_files(source.parent), {output.name.casefold()})
            output.write_bytes(b'modified')
            self.assertIsNone(cache.find_repair(source, 'settings-one'))
            output.write_bytes(b'repaired')
            source.write_bytes(b'changed animation')
            self.assertIsNone(cache.find_repair(source, 'settings-one'))
            source.write_bytes(b'original animation')
            self.assertEqual(cache.find_repair(source, 'settings-one'), record)
            output.unlink()
            self.assertIsNone(cache.find_repair(source, 'settings-one'))

    def test_multiple_settings_survive_folder_move_and_failed_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / 'run'
            run.mkdir()
            source = run / 'motion.bvh'
            source.write_bytes(b'original')
            for options in ('one', 'two'):
                cache.save_repair(source, options, {}, lambda p: p.write_bytes(b'repaired'))
            self.assertEqual(cache.find_repair(source,('one','two'))['options_sha'],'one')
            self.assertEqual(cache.find_repair(source,('missing','two'))['options_sha'],'two')
            self.assertIsNone(cache.find_repair(source,('missing','other')))
            before = cache.metadata_path(source).read_bytes()
            files = set(run.iterdir())
            def fail(path):
                path.write_bytes(b'incomplete')
                raise OSError('disk full')
            with self.assertRaisesRegex(OSError, 'disk full'):
                cache.save_repair(source, 'three', {}, fail)
            self.assertEqual(set(run.iterdir()), files)
            self.assertEqual(cache.metadata_path(source).read_bytes(), before)
            moved = root / 'renamed'
            run.rename(moved)
            self.assertIsNotNone(cache.find_repair(moved / source.name, 'one'))
            self.assertIsNotNone(cache.find_repair(moved / source.name, 'two'))

    def test_metadata_cannot_reference_source_or_files_outside_its_folder(self):
        source = Path('motion.bvh')
        for name in ('motion.bvh', '../outside.bvh', 'C:\\outside.bvh', '/outside.bvh'):
            with self.subTest(name=name):
                self.assertIsNone(cache.repair_path(source, {'file': name}))
