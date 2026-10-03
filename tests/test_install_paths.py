"""Installed entrypoints keep personal data beside the visible EXE."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_radar.__main__ import main as radar_main
from github_radar.install_paths import default_data_dir, install_dir


class InstallPathTests(unittest.TestCase):
    def test_development_data_stays_beside_package(self):
        package_file = Path.cwd() / "sample-development" / "github_radar" / "__main__.py"
        self.assertEqual(
            default_data_dir(package_file, Path(sys.executable), False),
            Path.cwd() / "sample-development" / "UserData",
        )

    def test_frozen_data_is_sibling_of_visible_exe(self):
        package_file = Path.cwd() / "sample-install" / "AppFiles" / "github_radar" / "__main__.py"
        executable = Path.cwd() / "sample-install" / "GitHubRadar.exe"
        self.assertEqual(default_data_dir(package_file, executable, True),
                         Path.cwd() / "sample-install" / "UserData")

    def test_install_root_keeps_chinese_and_spaces(self):
        executable = Path.cwd() / "我的软件 StarTrail" / "GitHubRadar.exe"
        self.assertEqual(install_dir(executable), Path.cwd() / "我的软件 StarTrail")

    def test_frozen_main_uses_sibling_data_for_browser(self):
        with tempfile.TemporaryDirectory(prefix="雷达 安装 ") as temporary:
            executable = Path(temporary) / "GitHubRadar.exe"
            seen = []
            with patch.object(sys, "executable", str(executable)), patch.object(
                    sys, "frozen", True, create=True):
                result = radar_main([], launcher=lambda _service, store: seen.append(
                    store.data_dir) or 0)
            self.assertEqual(result, 0)
            self.assertEqual(seen, [Path(temporary).resolve() / "UserData"])

    def test_frozen_scheduled_mode_uses_same_data_directory(self):
        with tempfile.TemporaryDirectory(prefix="雷达 安装 ") as temporary:
            executable = Path(temporary) / "GitHubRadar.exe"
            seen = []

            def handoff(store):
                seen.append(store.data_dir)
                return "skipped"

            with patch.object(sys, "executable", str(executable)), patch.object(
                    sys, "frozen", True, create=True), patch(
                    "github_radar.browser_launcher.handoff_scheduled_refresh", handoff):
                result = radar_main(["--scheduled-refresh"], client=object())
            self.assertEqual(result, 0)
            self.assertEqual(seen, [Path(temporary).resolve() / "UserData"])

    def test_entrypoint_runs_existing_app_when_named_github_radar(self):
        script = Path(__file__).resolve().parents[1] / "packaging" / "entrypoint.py"
        spec = importlib.util.spec_from_file_location("radar_packaging_entrypoint", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(sys, "executable", str(Path.cwd() / "sample-install" / "GitHubRadar.exe")), patch(
                "github_radar.__main__.main", return_value=7):
            self.assertEqual(module.main(["--help"]), 7)


if __name__ == "__main__":
    unittest.main()
