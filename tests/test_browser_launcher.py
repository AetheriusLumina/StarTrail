import json
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from github_radar import browser_launcher
from github_radar.browser_launcher import launch_browser_app
from github_radar.service import RadarService
from github_radar.storage import RadarStore


class NoNetworkClient:
    def __getattr__(self, name):
        raise AssertionError(f"Startup must not fetch GitHub: {name}")


class BrowserLauncherTests(unittest.TestCase):
    def test_default_opens_normal_browser_and_reuses_backend(self):
        with patch('github_radar.browser_launcher.webbrowser.open',side_effect=self.opener) as opener:
            thread=threading.Thread(target=launch_browser_app,args=(self.service,self.store),daemon=True)
            self.threads.append(thread);thread.start();self.wait_until(lambda:len(self.opened)==1)
            self.assertEqual(launch_browser_app(self.service,self.store),0)
            self.wait_until(lambda:len(self.opened)==2)
            self.assertEqual(self.opened[0],self.opened[1]);self.assertEqual(opener.call_count,2)
            self.stop_launchers()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RadarStore(Path(self.temp.name) / "UserData")
        self.store.save_auto_update_settings(False, "09:00")
        self.service = RadarService(NoNetworkClient(), self.store)
        self.opened = []
        self.threads = []
        self.addCleanup(self.stop_launchers)

    def opener(self, url):
        self.opened.append(url)
        return True

    def wait_until(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("Timed out waiting for launcher")

    def start_launch(self):
        thread = threading.Thread(
            target=launch_browser_app, args=(self.service, self.store),
            kwargs={"opener": self.opener}, daemon=True,
        )
        self.threads.append(thread)
        thread.start()
        return thread

    def stop_launchers(self):
        if self.opened:
            parts = urlsplit(self.opened[0])
            token = parse_qs(parts.fragment)["token"][0]
            base = f"{parts.scheme}://{parts.netloc}"
            request = Request(base + "/api/quit", data=b"{}", headers={
                "X-Radar-Token": token, "Origin": base,
                "Content-Type": "application/json",
            })
            try:
                with urlopen(request, timeout=2):
                    pass
            except OSError:
                pass
        for thread in self.threads:
            thread.join(timeout=3)

    def test_first_launch_opens_page_and_exits_only_after_quit(self):
        thread = self.start_launch()
        self.wait_until(lambda: len(self.opened) == 1)
        self.assertTrue(thread.is_alive())
        record = json.loads((self.store.data_dir / "radar-running.json").read_text())
        self.assertEqual(record["port"], urlsplit(self.opened[0]).port)
        self.assertEqual(record["token"], parse_qs(urlsplit(self.opened[0]).fragment)["token"][0])
        self.stop_launchers()
        self.assertFalse(thread.is_alive())
        self.assertFalse((self.store.data_dir / "radar-running.json").exists())

    def test_second_launch_reopens_verified_existing_page(self):
        first = self.start_launch()
        self.wait_until(lambda: len(self.opened) == 1)
        second = self.start_launch()
        self.wait_until(lambda: len(self.opened) == 2)
        second.join(timeout=2)
        self.assertFalse(second.is_alive())
        self.assertTrue(first.is_alive())
        self.assertEqual(self.opened[0], self.opened[1])

    def test_scheduled_trigger_hands_off_to_open_page_without_opening_another(self):
        self.assertTrue(hasattr(browser_launcher, "handoff_scheduled_refresh"))
        self.start_launch()
        self.wait_until(lambda: len(self.opened) == 1)
        self.store.save_auto_update_settings(True, "00:00")
        self.assertEqual(browser_launcher.handoff_scheduled_refresh(self.store, wait_seconds=3),
                         "error")
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.store.auto_attempts(date.today().isoformat()).attempts, 1)

    def test_stale_record_is_replaced_before_opening(self):
        record = self.store.data_dir / "radar-running.json"
        record.write_text(json.dumps({"port": 1, "pid": 999999, "token": "stale"}))
        self.start_launch()
        self.wait_until(lambda: len(self.opened) == 1)
        self.assertNotEqual(urlsplit(self.opened[0]).port, 1)
        self.assertNotEqual(json.loads(record.read_text())["token"], "stale")

    def test_scheduler_registration_failure_still_opens_saved_data_page(self):
        from github_radar.windows_scheduler import SchedulerError

        class FailingScheduler:
            def sync(inner, enabled, time):
                raise SchedulerError("Access denied")

            def status(inner, enabled, time):
                return False

        thread = threading.Thread(target=launch_browser_app,
                                  args=(self.service, self.store),
                                  kwargs={"opener": self.opener,
                                          "scheduler": FailingScheduler()}, daemon=True)
        self.threads.append(thread)
        thread.start()
        self.wait_until(lambda: len(self.opened) == 1)
        self.assertTrue(thread.is_alive())

    def test_launcher_checks_due_update_after_server_starts(self):
        from github_radar.browser_server import BrowserServer

        with patch.object(BrowserServer, "start_due_update", return_value=False) as check:
            self.start_launch()
            self.wait_until(lambda: len(self.opened) == 1)
            self.assertEqual(check.call_count, 1)

    def test_near_simultaneous_launches_share_one_server(self):
        barrier = threading.Barrier(3)

        def launch():
            barrier.wait()
            launch_browser_app(self.service, self.store, opener=self.opener)

        for _ in range(2):
            thread = threading.Thread(target=launch, daemon=True)
            self.threads.append(thread)
            thread.start()
        barrier.wait()
        self.wait_until(lambda: len(self.opened) == 2)
        self.assertEqual(self.opened[0], self.opened[1])
        self.assertEqual(sum(thread.is_alive() for thread in self.threads), 1)


if __name__ == "__main__":
    unittest.main()
