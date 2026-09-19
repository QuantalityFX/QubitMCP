from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from echograph.motion.backends.priormdm import normalization_assets as assets


def npy_bytes(values):
    stream = io.BytesIO()
    np.save(stream, values, allow_pickle=False)
    return stream.getvalue()


class NormalizationAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "app/downloads/humanml3d"
        self.dataset = self.root / "app/dataset/HumanML3D"
        self.revision = "a" * 40
        self.contents = {"Mean.npy": npy_bytes(np.zeros(263)), "Std.npy": npy_bytes(np.ones(263))}

    def fetch(self, url, limit):
        if url.endswith("/git/ref/heads/main"):
            return json.dumps({"object": {"type": "commit", "sha": self.revision}}).encode()
        if "/git/trees/" in url:
            self.assertIn(self.revision, url)
            # The tree SHA differs from the commit SHA used for raw downloads.
            entries = [{"type": "blob", "path": "HumanML3D/" + name,
                        "sha": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()}
                       for name, data in self.contents.items()]
            return json.dumps({"sha": "b" * 40, "tree": entries}).encode()
        self.assertIn(f"/{self.revision}/HumanML3D/", url)
        return self.contents[url.rsplit("/", 1)[-1]]

    def test_downloads_pair_then_reuses_installed_and_cached_data_offline(self):
        with patch.object(assets, "_fetch", side_effect=self.fetch) as fetch:
            assets.ensure_normalization(self.dataset, self.cache)
            self.assertEqual(fetch.call_count, 4)
        timestamps = {name: (self.dataset / name).stat().st_mtime_ns for name in assets.NAMES}
        with patch.object(assets, "_fetch", side_effect=AssertionError("must work offline")):
            assets.ensure_normalization(self.dataset, self.cache)
            restored = self.root / "another-install"
            assets.ensure_normalization(restored, self.cache)
        for name, content in self.contents.items():
            self.assertEqual((restored / name).read_bytes(), content)
            self.assertEqual((self.dataset / name).stat().st_mtime_ns, timestamps[name])

    def test_failed_second_download_resumes_first_from_cache(self):
        def interrupted(url, limit):
            if url.endswith("Std.npy"):
                raise OSError("connection interrupted")
            return self.fetch(url, limit)
        with patch.object(assets, "_fetch", side_effect=interrupted):
            with self.assertRaises(OSError):
                assets.ensure_normalization(self.dataset, self.cache)
        self.assertFalse(self.dataset.exists())
        self.assertTrue((self.cache / "Mean.npy").is_file())
        with patch.object(assets, "_fetch", side_effect=self.fetch) as fetch:
            assets.ensure_normalization(self.dataset, self.cache)
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(fetch.call_args.args[0].endswith("Std.npy"))

    def test_checksum_mismatch_never_installs_unverified_data(self):
        def corrupt(url, limit):
            if url.endswith("Mean.npy"):
                return b"corrupted download"
            return self.fetch(url, limit)
        with patch.object(assets, "_fetch", side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, "checksum"):
                assets.ensure_normalization(self.dataset, self.cache)
        self.assertFalse(self.dataset.exists())
        self.assertFalse((self.cache / "Mean.npy").exists())

    def test_download_validation_rejects_bad_shapes_and_nonpositive_std(self):
        for values in (np.ones(262), np.full(263, np.nan), np.zeros(263)):
            with self.subTest(values=values[:2]):
                with self.assertRaises(ValueError):
                    assets._validate(npy_bytes(values), "Std.npy")
        pickled = io.BytesIO()
        np.save(pickled, np.array([{}], dtype=object), allow_pickle=True)
        with self.assertRaises(ValueError):
            assets._validate(pickled.getvalue(), "Mean.npy")

    def test_custom_pair_is_preserved_and_incomplete_custom_pair_is_not_mixed(self):
        self.dataset.mkdir(parents=True)
        custom_mean = npy_bytes(np.full(263, 42.0))
        (self.dataset / "Mean.npy").write_bytes(custom_mean)
        with patch.object(assets, "_fetch", side_effect=self.fetch):
            with self.assertRaisesRegex(ValueError, "preserved"):
                assets.ensure_normalization(self.dataset, self.cache)
        self.assertFalse((self.dataset / "Std.npy").exists())
        (self.dataset / "Std.npy").write_bytes(npy_bytes(np.full(263, 2.0)))
        with patch.object(assets, "_fetch", side_effect=AssertionError("custom data must not fetch")):
            assets.ensure_normalization(self.dataset, self.cache)
        self.assertEqual((self.dataset / "Mean.npy").read_bytes(), custom_mean)

    def test_evaluator_statistics_are_not_accepted_as_source(self):
        def wrong_tree(url, limit):
            result = self.fetch(url, limit)
            if "/git/trees/" in url:
                result = result.replace(b"HumanML3D/Mean.npy", b"dataset/t2m_mean.npy")
            return result
        with patch.object(assets, "_fetch", side_effect=wrong_tree):
            with self.assertRaisesRegex(ValueError, "expected Mean.npy/Std.npy pair"):
                assets.ensure_normalization(self.dataset, self.cache)
        self.assertFalse(self.dataset.exists())


if __name__ == "__main__":
    unittest.main()
