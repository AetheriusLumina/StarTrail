"""Redistributable bundles retain notices without local build paths."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class LicenseCollectionTests(unittest.TestCase):
    def module(self):
        script = ROOT / 'scripts/collect_licenses.py'
        self.assertTrue(script.is_file(), 'Missing license collection')
        spec = importlib.util.spec_from_file_location('collect_licenses', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_manifest_has_hashes_and_no_build_paths(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / 'bundle'
            output.mkdir()
            (output / 'license.txt').write_text('Public legal text', encoding='utf-8')
            module.write_manifest(output, [{'name': 'example', 'version': '1.0'}])
            text = (output / 'manifest.json').read_text(encoding='utf-8')
            manifest = json.loads(text)
            self.assertEqual(manifest['files'][0]['file'], 'license.txt')
            self.assertEqual(len(manifest['files'][0]['sha256']), 64)
            self.assertNotIn(str(base), text)

    def test_required_upstream_notice_absence_stops_build(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module, 'ROOT', Path(directory)):
                with self.assertRaisesRegex(FileNotFoundError, 'PUBLIC_NOTICES_MISSING'):
                    module.collect(Path(directory) / 'destination', Path(directory) / 'models')


if __name__ == '__main__':
    unittest.main()
