"""Run the same updater interaction checks in local and Windows CI suites."""
import subprocess
import unittest
from pathlib import Path

class SoftwareUpdateUITests(unittest.TestCase):
    def test_timed_notice_confirmation_and_source_mode(self):
        result=subprocess.run(['node',str(Path(__file__).with_name('software_update_ui.cjs'))],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
