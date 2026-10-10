"""The installer may reuse retained data only with an ownership marker."""

import unittest
from pathlib import Path


class InstallerDataSafetyTests(unittest.TestCase):
    def test_single_markdown_user_guide_is_packaged_and_legacy_copy_removed(self):
        root = Path(__file__).resolve().parents[1]
        build = (root / "packaging/build.ps1").read_text(encoding="utf-8")
        script = (root / "packaging/GitHubRadar.iss").read_text(encoding="utf-8")
        self.assertFalse((root / "使用说明.txt").exists())
        self.assertIn("docs\\USER_GUIDE.md", build)
        self.assertIn("使用说明.md", build)
        files = script.split("[Files]", 1)[1].split("[InstallDelete]", 1)[0]
        self.assertIn("使用说明.md", files)
        self.assertNotIn("使用说明.txt", files)
        cleanup = script.split("[InstallDelete]", 1)[1].split("[UninstallDelete]", 1)[0]
        self.assertIn('Type: files; Name: "{app}\\使用说明.txt"', cleanup)
        self.assertNotIn("UserData", cleanup)

    def test_existing_userdata_requires_installer_marker(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "packaging" / "GitHubRadar.iss").read_text(encoding="utf-8")
        build = (root / "packaging" / "build.ps1").read_text(encoding="utf-8")

        self.assertIn("UserData\\.github-radar-data", script)
        self.assertIn("UserData\\.github-radar-data", build)
        self.assertIn("onlyifdoesntexist", script)
        reuse_check = script.split("function HasUnrelatedFiles", 1)[1].split(
            "function DestinationIsSafe", 1)[0]
        self.assertIn("FileExists", reuse_check)
        self.assertIn(".github-radar-data", reuse_check)

    def test_installer_records_its_actual_native_uninstaller(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "packaging" / "GitHubRadar.iss").read_text(encoding="utf-8")
        self.assertIn("ExpandConstant('{uninstallexe}')", script)
        self.assertIn("github-radar-uninstaller.txt", script)
        self.assertIn("[UninstallDelete]", script)
        self.assertIn("RaiseException(ExpandConstant('{cm:UninstallLinkFailed}'))", script)

    def test_upgrade_accepts_installations_with_numbered_uninstaller(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "packaging" / "GitHubRadar.iss").read_text(encoding="utf-8")
        upgrade = script.split("procedure InitializeWizard", 1)[1].split(
            "function ShouldSkipPage", 1)[0]
        self.assertIn("HasRecordedUninstaller(CandidateRoot)", upgrade)


if __name__ == "__main__":
    unittest.main()
