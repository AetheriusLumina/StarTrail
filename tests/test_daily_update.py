"""Daily update policy and persistence do not need GitHub or Windows tasks."""

import tempfile
import unittest
import sqlite3
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from github_radar import daily_update
from github_radar.daily_update import AutoAttemptState, AutoUpdateSettings, auto_update_due
from github_radar.storage import RadarStore


class DailyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / "UserData"
        self.store = RadarStore(self.data_dir)

    def test_defaults_save_and_reopen(self):
        self.assertEqual(self.store.load_auto_update_settings(),
                         AutoUpdateSettings(True, "09:00"))
        self.store.save_auto_update_settings(False, "20:30")
        self.assertEqual(RadarStore(self.data_dir).load_auto_update_settings(),
                         AutoUpdateSettings(False, "20:30"))

    def test_invalid_schedule_does_not_change_saved_setting(self):
        for enabled, time in (("yes", "09:00"), (1, "09:00"), (True, "9:00"),
                              (True, "22:00"), (True, "23:59"), (True, "bad")):
            with self.subTest(enabled=enabled, time=time), self.assertRaises(ValueError):
                self.store.save_auto_update_settings(enabled, time)
        self.assertEqual(self.store.load_auto_update_settings(),
                         AutoUpdateSettings(True, "09:00"))

    def test_due_policy_respects_time_success_and_hourly_retry(self):
        settings = AutoUpdateSettings(True, "09:00")
        empty = AutoAttemptState(0, None, None, None)
        at = lambda value: datetime.fromisoformat("2026-09-28T" + value + "+08:00")
        self.assertFalse(auto_update_due(at("08:59"), settings, empty, None))
        self.assertTrue(auto_update_due(at("09:00"), settings, empty, None))
        self.assertFalse(auto_update_due(at("10:00"), settings, empty, "2026-09-28"))
        self.assertFalse(auto_update_due(at("10:00"), AutoUpdateSettings(False, "09:00"),
                                         empty, None))
        failed = AutoAttemptState(1, "2026-09-28T09:00:00+08:00", "error", "offline")
        self.assertFalse(auto_update_due(at("09:59"), settings, failed, None))
        self.assertTrue(auto_update_due(at("10:00"), settings, failed, None))
        self.assertFalse(auto_update_due(at("12:00"), settings,
                                         AutoAttemptState(3, failed.last_attempt_at,
                                                          "error", "offline"), None))

    def test_attempts_are_atomic_and_survive_restart(self):
        day = "2026-09-28"
        at = "2026-09-28T09:00:00+08:00"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: RadarStore(self.data_dir).begin_auto_attempt(day, at),
                                    range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.store.finish_auto_attempt(day, "error", "offline")
        reopened = RadarStore(self.data_dir)
        self.assertEqual(reopened.auto_attempts(day),
                         AutoAttemptState(1, at, "error", "offline"))
        self.assertFalse(reopened.begin_auto_attempt(day, "2026-09-28T09:59:59+08:00"))
        self.assertTrue(reopened.begin_auto_attempt(day, "2026-09-28T10:00:00+08:00"))
        reopened.finish_auto_attempt(day, "error", "rate limit")
        self.assertTrue(reopened.begin_auto_attempt(day, "2026-09-28T11:00:00+08:00"))
        reopened.finish_auto_attempt(day, "error", "offline")
        self.assertFalse(reopened.begin_auto_attempt(day, "2026-09-28T12:00:00+08:00"))
        self.assertEqual(RadarStore(self.data_dir).auto_attempts(day).attempts, 3)

    def test_manual_success_suppresses_automatic_attempt(self):
        day = "2026-09-28"
        at = "2026-09-28T08:30:00+08:00"
        self.store.commit_daily(day, [], [], [], completed_at=at)
        self.assertFalse(self.store.begin_auto_attempt(day, "2026-09-28T09:00:00+08:00"))
        self.assertEqual(self.store.auto_attempts(day).attempts, 0)

    def test_scheduled_success_runs_once_and_repeated_trigger_makes_no_request(self):
        self.assertTrue(hasattr(daily_update, "run_scheduled_update"))
        calls = []

        class Service:
            def refresh(inner, day, observed_at):
                calls.append(day)
                self.store.commit_daily(day, [], [], [], completed_at=observed_at)
                return SimpleNamespace(status="ok", message="更新完成")

        now = datetime.fromisoformat("2026-09-28T09:00:00+08:00")
        self.assertEqual(daily_update.run_scheduled_update(self.store, Service(), now), "success")
        self.assertEqual(daily_update.run_scheduled_update(self.store, Service(), now), "skipped")
        self.assertEqual(calls, ["2026-09-28"])
        self.assertEqual(self.store.auto_attempts("2026-09-28").attempts, 1)

    def test_scheduled_failure_keeps_old_data_and_records_reason(self):
        self.assertTrue(hasattr(daily_update, "run_scheduled_update"))
        self.store.commit_daily("2026-09-27", [], [], [],
                                completed_at="2026-09-27T09:00:00+08:00")

        class Service:
            def refresh(inner, day, observed_at):
                return SimpleNamespace(status="error", message="GitHub 暂不可用")

        now = datetime.fromisoformat("2026-09-28T09:00:00+08:00")
        self.assertEqual(daily_update.run_scheduled_update(self.store, Service(), now), "error")
        self.assertEqual(self.store.latest_successful_date(), "2026-09-27")
        self.assertEqual(self.store.auto_attempts("2026-09-28").reason,
                         "GitHub 暂不可用")
        self.assertEqual(self.store.latest_refresh_failure(),
                         (now.isoformat(timespec="seconds"), "GitHub 暂不可用"))
        self.assertIn("GitHub 暂不可用",
                      (self.data_dir / "radar.log").read_text(encoding="utf-8"))

    def test_disk_write_failure_exits_worker_without_losing_old_data(self):
        self.store.commit_daily("2026-09-27", [], [], [],
                                completed_at="2026-09-27T09:00:00+08:00")

        class Service:
            def refresh(inner, day, observed_at):
                self.fail("GitHub must not be called when attempt cannot be saved")

        now = datetime.fromisoformat("2026-09-28T09:00:00+08:00")
        with patch.object(self.store, "begin_auto_attempt",
                          side_effect=sqlite3.OperationalError("disk full")):
            self.assertEqual(daily_update.run_scheduled_update(self.store, Service(), now),
                             "error")
        self.assertEqual(self.store.latest_successful_date(), "2026-09-27")

    def test_settings_can_show_latest_attempt_from_previous_day(self):
        day = "2026-09-27"
        at = "2026-09-27T09:00:00+08:00"
        self.assertTrue(self.store.begin_auto_attempt(day, at))
        self.store.finish_auto_attempt(day, "error", "离线")
        self.assertTrue(hasattr(self.store, "latest_auto_attempt"))
        self.assertEqual(self.store.latest_auto_attempt().last_attempt_at, at)


if __name__ == "__main__":
    unittest.main()
