import tempfile
import sys
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
from unittest.mock import patch

from github_radar.__main__ import main
from github_radar.models import Repository, StarDay
from github_radar.storage import RadarStore
from github_radar.update_lock import update_lock


class CliTests(unittest.TestCase):
    def test_failure_message_does_not_crash_english_windows_console(self):
        from github_radar.windows_scheduler import SchedulerError
        buffer = BytesIO()
        console = TextIOWrapper(buffer, encoding='cp1252')
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(console), patch(
                'github_radar.__main__._scheduler_for', side_effect=SchedulerError('注册失败')):
            result = main(['--data-dir', directory, '--sync-scheduler'])
        console.flush()
        self.assertEqual(result, 1)
        self.assertTrue(buffer.getvalue(), 'Error feedback disappeared')

    def test_installer_can_restore_daily_task_without_opening_browser(self):
        with tempfile.TemporaryDirectory() as temp:
            install_root = (Path(temp) / "GitHub Radar").resolve()
            data = install_root / "UserData"
            store = RadarStore(data)
            store.save_auto_update_settings(True, "09:00")
            executable = install_root / "GitHubRadar.exe"
            with patch.object(sys, "executable", str(executable)), patch.object(
                    sys, "frozen", True, create=True), patch(
                    "github_radar.windows_scheduler.SchedulerController") as controller:
                code = main(["--data-dir", str(data), "--sync-scheduler"],
                            launcher=lambda *_: self.fail("Installer opened browser"))
            self.assertEqual(code, 0)
            self.assertEqual(controller.call_args.args[0], install_root)
            self.assertEqual(controller.call_args.args[1], executable)
            controller.return_value.sync.assert_called_once_with(True, "09:00")

    def test_scheduled_entry_uses_no_browser_or_ai(self):
        def no_browser(service, store):
            self.fail("Scheduled task opened a browser")

        with tempfile.TemporaryDirectory() as temp, patch(
                "github_radar.browser_launcher.handoff_scheduled_refresh", return_value=None), patch(
                "github_radar.daily_update.run_scheduled_update", return_value="success") as run:
            code = main(["--data-dir", str(Path(temp) / "UserData"),
                         "--scheduled-refresh"], launcher=no_browser)
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 1)

    def test_scheduled_entry_does_not_run_second_refresh_after_handoff(self):
        with tempfile.TemporaryDirectory() as temp, patch(
                "github_radar.browser_launcher.handoff_scheduled_refresh",
                return_value="success") as handoff, patch(
                "github_radar.daily_update.run_scheduled_update") as local:
            code = main(["--data-dir", str(Path(temp) / "UserData"),
                         "--scheduled-refresh"])
        self.assertEqual(code, 0)
        self.assertEqual(handoff.call_count, 1)
        local.assert_not_called()

    def test_browser_launch_receives_optional_ai_dependencies(self):
        with tempfile.TemporaryDirectory() as temp, patch(
                "github_radar.browser_launcher.launch_browser_app", return_value=0) as launch:
            code = main(["--data-dir", str(Path(temp) / "UserData")])
        self.assertEqual(code, 0)
        self.assertIn("ai_service", launch.call_args.kwargs)
        self.assertIn("connection", launch.call_args.kwargs)
        self.assertIn("scheduler", launch.call_args.kwargs)

    def test_no_arguments_launches_local_browser(self):
        called = []

        def launcher(service, store):
            called.append(store.db_path)
            return 0

        with tempfile.TemporaryDirectory() as temp:
            result = main(["--data-dir", str(Path(temp) / "UserData")], launcher=launcher)
        self.assertEqual(result, 0)
        self.assertEqual(called[0].name, "radar.db")

    def test_refresh_console_labels_verified_daily_additions(self):
        repository = Repository(1, "owner/project", "https://github.com/owner/project",
                                "Project", (), "Python", 1200, False)

        class Client:
            core_remaining = 60

            def search(self, query, **kwargs):
                return [repository] if query.startswith("created:") else []

            def star_history(self, full_name):
                return [StarDay("2026-09-25", 5)]

        output = StringIO()
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(output):
            code = main(["--data-dir", str(Path(temp) / "UserData"), "--refresh"],
                        client=Client(), local_date="2026-09-26",
                        observed_at="2026-09-26T08:00:00+08:00")
        self.assertEqual(code, 0)
        self.assertIn("新增 5 Star", output.getvalue())
        self.assertIn("2026-09-25", output.getvalue())

    def test_console_refresh_respects_running_update_lock(self):
        calls = []

        class Client:
            core_remaining = 60

            def search(inner, query, **kwargs):
                calls.append(query)
                return []

        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "UserData"
            data_dir.mkdir()
            with update_lock(data_dir), redirect_stdout(StringIO()):
                code = main(["--data-dir", str(data_dir), "--refresh"],
                            client=Client(), local_date="2026-09-28",
                            observed_at="2026-09-28T10:00:00+08:00")
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
