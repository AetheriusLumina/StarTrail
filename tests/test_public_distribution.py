"""Public development must not rely on the publisher's private workspace."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from github_radar import translation_worker

ROOT = Path(__file__).resolve().parents[1]


class PublicDistributionTests(unittest.TestCase):
    def test_source_models_use_this_checkout(self):
        with patch.dict(os.environ, {}, clear=True):
            first, second = translation_worker.model_paths()
        self.assertEqual(first, ROOT / '.tools' / 'translation-models' / 'en-zh')
        self.assertEqual(second, ROOT / '.tools' / 'translation-models' / 'zh-en')

    def test_explicit_model_directory_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, STARTRAIL_MODEL_DIR=directory):
                self.assertEqual(translation_worker.model_paths()[0], Path(directory) / 'en-zh')

    def test_frozen_models_ignore_source_override(self):
        with patch('sys.frozen', True, create=True), patch.dict(os.environ, STARTRAIL_MODEL_DIR='unused'):
            self.assertEqual(translation_worker.model_paths()[0],
                             Path(translation_worker.__file__).parent / 'translation_models' / 'en-zh')

    def test_brand_and_legacy_installer_identity(self):
        page = (ROOT / 'github_radar/web_assets/index.html').read_text(encoding='utf-8')
        installer = (ROOT / 'packaging/GitHubRadar.iss').read_text(encoding='utf-8')
        self.assertIn('StarTrail', page)
        self.assertNotIn('GitHub Radar', page)
        self.assertIn('AppName=StarTrail', installer)
        self.assertIn('AppId=GitHubRadar', installer)
        self.assertIn('Programs\\GitHubRadar', installer)
        self.assertIn('OutputBaseFilename=StarTrail_Setup', installer)

    def test_build_defaults_stay_inside_checkout(self):
        build = (ROOT / 'packaging/build.ps1').read_text(encoding='utf-8')
        self.assertIn("Join-Path $sourceRoot '.build'", build)
        self.assertIn("Join-Path $sourceRoot '.venv\\Scripts\\python.exe'", build)
        self.assertNotIn('..\\..\\outputs', build)
        contract = (ROOT / 'tests/project_translation_contract.cjs').read_text(encoding='utf-8')
        self.assertNotIn('D:/', contract)

    def test_public_dependency_metadata_exists(self):
        self.assertTrue((ROOT / 'pyproject.toml').is_file(), 'Missing independent package metadata')
        self.assertTrue((ROOT / 'requirements/constraints.txt').is_file(), 'Missing fixed dependencies')

    def test_brand_icon_is_in_browser_and_installer(self):
        page = (ROOT / 'github_radar/web_assets/index.html').read_text(encoding='utf-8')
        build = (ROOT / 'packaging/build.ps1').read_text(encoding='utf-8')
        self.assertTrue('/assets/startrail.png' in page, 'Browser icon missing')
        self.assertTrue('--icon' in build and 'startrail.ico' in build, 'Frozen icon missing')
        for name in ('startrail.png', 'startrail.ico'):
            self.assertTrue((ROOT / 'github_radar/web_assets' / name).is_file(), name)


if __name__ == '__main__':
    unittest.main()
