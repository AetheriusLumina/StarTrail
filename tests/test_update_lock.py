"""One UserData directory cannot be updated in two concurrent processes."""

import tempfile
import unittest
import importlib.util
import subprocess
import sys
import time
from pathlib import Path


class UpdateLockTests(unittest.TestCase):
    def test_second_holder_cannot_enter_until_first_releases(self):
        self.assertIsNotNone(importlib.util.find_spec("github_radar.update_lock"))
        from github_radar.update_lock import UpdateBusyError, update_lock
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            with update_lock(data_dir):
                with self.assertRaises(UpdateBusyError):
                    with update_lock(data_dir):
                        pass
            with update_lock(data_dir):
                pass

    def test_child_process_crash_releases_operating_system_lock(self):
        from github_radar.update_lock import UpdateBusyError, update_lock

        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            script = ("import sys, time\n"
                      "from pathlib import Path\n"
                      "from github_radar.update_lock import update_lock\n"
                      "with update_lock(Path(sys.argv[1])):\n"
                      " print('READY', flush=True)\n"
                      " time.sleep(30)\n")
            child = subprocess.Popen([sys.executable, "-c", script, str(data_dir)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "READY")
                with self.assertRaises(UpdateBusyError):
                    with update_lock(data_dir):
                        pass
            finally:
                child.kill()
                child.wait(timeout=5)
                child.stdout.close()
                child.stderr.close()
            deadline = time.monotonic() + 2
            while True:
                try:
                    with update_lock(data_dir):
                        break
                except UpdateBusyError:
                    if time.monotonic() >= deadline:
                        self.fail("OS lock was not released after the child exited")
                    time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
