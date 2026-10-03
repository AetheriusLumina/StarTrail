"""The release gate checks filenames as well as accidental embedded secrets."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicPrivacyTests(unittest.TestCase):
    def setUp(self):
        script = ROOT / 'scripts/check_public.py'
        self.assertTrue(script.is_file(), 'Public release safety check is missing')
        spec = importlib.util.spec_from_file_location('check_public', script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_realistic_secrets_and_home_paths_are_rejected_without_echoing_values(self):
        fake = 'ghp_' + 'a' * 36
        machine = chr(67) + ':' + '/' + 'Users' + '/someone/project'
        findings = self.module.inspect_file('github_radar/example.py', (fake + '\n' + machine).encode())
        self.assertIn('credential', findings)
        self.assertIn('absolute-path', findings)
        self.assertNotIn(fake, str(findings))

    def test_data_files_cannot_be_committed_even_if_gitignore_is_overridden(self):
        for name in ('UserData/radar.db', 'data/account.bin', 'cache.sqlite', '.env', 'build/Setup.exe'):
            self.assertIn('private-or-generated-file', self.module.inspect_file(name, b''), name)

    def test_public_client_id_and_repository_url_are_allowed(self):
        content = b'{"github_oauth_client_id":"publicclient12345678"}\nhttps://github.com/owner/repo'
        self.assertEqual(self.module.inspect_file('github_radar/public_config.json', content), [])

    def test_personal_email_is_rejected(self):
        email = 'someone' + '@' + 'gmail.com'
        self.assertIn('personal-email', self.module.inspect_file('README.md', email.encode()))


    def test_jpeg_screenshot_remains_subject_to_private_directory_gate(self):
        jpeg = b'\xff\xd8\xff\xe0screenshot'
        self.assertEqual(self.module.inspect_file('docs/images/home.jpg', jpeg), [])
        self.assertEqual(self.module.inspect_file('docs/images/home.jpeg', jpeg), [])
        self.assertIn('private-or-generated-file', self.module.inspect_file('UserData/private.jpg', jpeg))


if __name__ == '__main__':
    unittest.main()
