"""Uninstall must close this app and remove only this installation's task."""

import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from github_radar.__main__ import main as radar_main
from github_radar.browser_launcher import launch_browser_app
from github_radar.service import RadarService
from github_radar.storage import RadarStore
from github_radar.uninstall import (UninstallError, native_uninstaller_path,
                                    prepare_uninstall)
from github_radar.windows_scheduler import SchedulerController, build_task_xml, task_name


class NoNetworkClient:
    def __getattr__(self, name):
        raise AssertionError(f"Uninstall must not fetch GitHub: {name}")


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="雷达 卸载 ")
        self.addCleanup(self.temporary.cleanup)
        self.install = Path(self.temporary.name) / "GitHub Radar"
        self.install.mkdir()
        self.data = self.install / "UserData"
        self.store = RadarStore(self.data)
        self.store.save_auto_update_settings(False, "09:00")
        self.command = self.install / "GitHubRadar.exe"
        self.arguments = ("--scheduled-refresh", "--data-dir", str(self.data))

    def _scheduler_factory(self, runner):
        def factory(*args, **kwargs):
            kwargs["runner"] = runner
            return SchedulerController(*args, **kwargs)
        return factory

    def _missing_task_runner(self, args, **_kwargs):
        return subprocess.CompletedProcess(args, 0x80070002, stdout="", stderr="")

    def test_running_browser_is_told_to_quit_and_finishes_before_return(self):
        service = RadarService(NoNetworkClient(), self.store)
        opened = []
        thread = threading.Thread(target=launch_browser_app,
                                  args=(service, self.store),
                                  kwargs={"opener": lambda url: opened.append(url)},
                                  daemon=True)
        thread.start()
        self.addCleanup(lambda: thread.join(timeout=2))
        deadline = time.monotonic() + 4
        while not opened and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(opened)

        with patch("github_radar.uninstall.SchedulerController",
                   self._scheduler_factory(self._missing_task_runner)):
            prepare_uninstall(self.install, self.data, timeout_seconds=4)

        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertFalse((self.data / "radar-running.json").exists())

    def test_stale_record_does_not_prevent_own_task_cleanup(self):
        (self.data / "radar-running.json").write_text(json.dumps({
            "port": 2, "pid": 123, "token": "old", "instance_id": "old",
        }), encoding="utf-8")
        self.store.save_scheduler_binding({
            "install_dir": str(self.install), "command": str(self.command),
            "arguments": list(self.arguments),
        })
        with patch("github_radar.uninstall.SchedulerController",
                   self._scheduler_factory(self._missing_task_runner)):
            prepare_uninstall(self.install, self.data, timeout_seconds=0.1)
        self.assertIsNone(self.store.load_scheduler_binding())

    def test_foreign_task_cannot_be_deleted(self):
        foreign = build_task_xml(self.install / "Other.exe", self.arguments,
                                 self.install, "09:00", "S-1-5-21-123")
        calls = []

        def runner(args, **_kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout=foreign, stderr="")

        with patch("github_radar.uninstall.SchedulerController",
                   self._scheduler_factory(runner)):
            with self.assertRaises(UninstallError):
                prepare_uninstall(self.install, self.data, timeout_seconds=0.1)
        self.assertTrue(calls)
        self.assertFalse(any("/delete" in call for call in calls))

    def test_copied_personal_data_cannot_delete_other_installations_task(self):
        other = self.install.parent / "Another live Radar"
        other_command = other / "GitHubRadar.exe"
        other_arguments = ("--scheduled-refresh", "--data-dir",
                           str(other / "UserData"))
        self.store.save_scheduler_binding({
            "install_dir": str(other), "command": str(other_command),
            "arguments": list(other_arguments),
        })
        other_name = task_name(other)
        other_xml = build_task_xml(other_command, other_arguments, other,
                                   "09:00", "S-1-5-21-123")
        calls = []

        def runner(args, **_kwargs):
            calls.append(args)
            if args[1] == "/query":
                return subprocess.CompletedProcess(
                    args, 0 if args[args.index("/tn") + 1] == other_name else 0x80070002,
                    stdout=other_xml if args[args.index("/tn") + 1] == other_name else "",
                    stderr="")
            self.fail("Uninstall of this copy must not delete another installation's task")

        with patch("github_radar.uninstall.SchedulerController",
                   self._scheduler_factory(runner)):
            prepare_uninstall(self.install, self.data, timeout_seconds=0.1)
        self.assertFalse(any("/delete" in call for call in calls))
        self.assertIsNone(self.store.load_scheduler_binding())

    def test_unresponsive_running_instance_times_out_without_task_delete(self):
        calls = []

        def runner(args, **_kwargs):
            calls.append(args)
            return self._missing_task_runner(args)

        with patch("github_radar.uninstall._lock_free", return_value=False), patch(
                "github_radar.uninstall.SchedulerController",
                self._scheduler_factory(runner)):
            with self.assertRaisesRegex(UninstallError, "退出"):
                prepare_uninstall(self.install, self.data, timeout_seconds=0.05)
        self.assertEqual(calls, [])

    def test_corrupt_personal_database_reports_cleanup_failure(self):
        (self.data / "radar.db").write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(UninstallError, "个人数据"):
            prepare_uninstall(self.install, self.data, timeout_seconds=0.1)

    def test_native_uninstaller_accepts_only_this_appfiles_exe(self):
        appfiles = self.install / "AppFiles"
        appfiles.mkdir()
        native = appfiles / "unins000.exe"
        native.write_bytes(b"test")
        self.assertEqual(native_uninstaller_path(
            self.install, f'"{native}" /SILENT'), native)
        outside = self.install.parent / "unins000.exe"
        outside.write_bytes(b"test")
        with self.assertRaises(UninstallError):
            native_uninstaller_path(self.install, f'"{outside}"')

    def test_launcher_uses_own_native_uninstaller_without_registry(self):
        from github_radar.uninstall import launch_uninstaller

        appfiles = self.install / "AppFiles"
        appfiles.mkdir()
        native = appfiles / "unins000.exe"
        native.write_bytes(b"test")
        (appfiles / "unins000.dat").write_bytes(b"test")
        (appfiles / "github-radar-uninstaller.txt").write_text(
            native.name, encoding="utf-8")
        with patch("winreg.OpenKey",
                   side_effect=FileNotFoundError("stale registration")), patch(
                       "github_radar.uninstall.subprocess.Popen") as process, patch(
                           "github_radar.uninstall.ctypes.windll.user32.MessageBoxW"):
            self.assertEqual(launch_uninstaller(self.install), 0)
        process.assert_called_once_with([str(native)], cwd=str(self.install))

    def test_launcher_ignores_registry_pointing_to_another_install(self):
        from github_radar.uninstall import launch_uninstaller

        appfiles = self.install / "AppFiles"
        appfiles.mkdir()
        native = appfiles / "unins000.exe"
        native.write_bytes(b"test")
        (appfiles / "unins000.dat").write_bytes(b"test")
        (appfiles / "github-radar-uninstaller.txt").write_text(
            native.name, encoding="utf-8")
        with patch("winreg.OpenKey") as registry, patch(
                "github_radar.uninstall.subprocess.Popen") as process, patch(
                    "github_radar.uninstall.ctypes.windll.user32.MessageBoxW"):
            self.assertEqual(launch_uninstaller(self.install), 0)
        registry.assert_not_called()
        process.assert_called_once_with([str(native)], cwd=str(self.install))

    def test_launcher_uses_recorded_uninstaller_not_first_numbered_file(self):
        from github_radar.uninstall import launch_uninstaller

        appfiles = self.install / "AppFiles"
        appfiles.mkdir()
        for name in ("unins000", "unins001"):
            (appfiles / f"{name}.exe").write_bytes(b"test")
            (appfiles / f"{name}.dat").write_bytes(b"test")
        native = appfiles / "unins001.exe"
        (appfiles / "github-radar-uninstaller.txt").write_text(
            native.name, encoding="utf-8")
        with patch("github_radar.uninstall.subprocess.Popen") as process:
            self.assertEqual(launch_uninstaller(self.install), 0)
        process.assert_called_once_with([str(native)], cwd=str(self.install))

    def test_launcher_rejects_record_pointing_outside_install(self):
        from github_radar.uninstall import launch_uninstaller

        appfiles = self.install / "AppFiles"
        appfiles.mkdir()
        (appfiles / "github-radar-uninstaller.txt").write_text(
            "..\\another.exe", encoding="utf-8")
        with patch("github_radar.uninstall.subprocess.Popen") as process, patch(
                "github_radar.uninstall.ctypes.windll.user32.MessageBoxW"):
            self.assertEqual(launch_uninstaller(self.install), 1)
        process.assert_not_called()

    def test_cli_preparation_returns_nonzero_on_cleanup_failure(self):
        with patch("github_radar.uninstall.prepare_uninstall",
                   side_effect=UninstallError("任务无法删除")):
            self.assertNotEqual(radar_main(["--data-dir", str(self.data),
                                            "--prepare-uninstall"]), 0)


if __name__ == "__main__":
    unittest.main()
