"""Model preparation verifies archives before extracting any files."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class ModelPreparationTests(unittest.TestCase):
    def setUp(self):
        script = ROOT / 'scripts/prepare_models.py'
        self.assertTrue(script.is_file(), 'Public model preparation is missing')
        spec = importlib.util.spec_from_file_location('prepare_models', script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def archive(self, root, missing=False):
        archive = root / 'test.argosmodel'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('model-package/metadata.json', '{}')
            if not missing:
                z.writestr('model-package/model/config.json', '{}')
            z.writestr('../../unexpected.txt', 'untrusted extra entry')
        model = {'prefix': 'model-package/', 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        return archive, model

    def test_matching_archive_extracts_only_required_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, model = self.archive(root)
            records = self.module.prepare_archive(archive, model, root / 'models',
                                                  ['metadata.json', 'model/config.json'])
            self.assertEqual(len(records), 2)
            self.assertEqual((root / 'models/model/config.json').read_text(), '{}')
            self.assertFalse((root / 'unexpected.txt').exists())

    def test_wrong_hash_leaves_existing_model_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, model = self.archive(root)
            model['sha256'] = '0' * 64
            (root / 'models').mkdir()
            original = root / 'models/metadata.json'
            original.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'):
                self.module.prepare_archive(archive, model, root / 'models', ['metadata.json'])
            self.assertEqual(original.read_text(), 'keep')

    def test_missing_member_does_not_partially_overwrite_old_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, model = self.archive(root, missing=True)
            (root / 'models').mkdir()
            original = root / 'models/metadata.json'
            original.write_text('keep')
            with self.assertRaisesRegex(ValueError, 'FILE_MISSING'):
                self.module.prepare_archive(archive, model, root / 'models',
                                            ['metadata.json', 'model/config.json'])
            self.assertEqual(original.read_text(), 'keep')

    def test_required_paths_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, model = self.archive(root)
            with self.assertRaisesRegex(ValueError, 'UNSAFE_PATH'):
                self.module.prepare_archive(archive, model, root / 'models', ['../outside'])

    def test_download_requires_https(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'HTTPS_REQUIRED'):
                self.module.download('http://example.com/model', Path(temp) / 'model')


if __name__ == '__main__':
    unittest.main()
