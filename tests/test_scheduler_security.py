"""Security regressions use XML and a fake runner, never Windows tasks."""
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from github_radar import windows_scheduler as scheduler

NS = {'t': scheduler._NS}

class SchedulerSecurityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_extended_tasks_are_not_accepted_or_modified(self):
        command = self.root / 'StarTrail' / 'GitHubRadar.exe'
        working = command.parent
        arguments = ('--scheduled-refresh',)
        original = scheduler.build_task_xml(command, arguments, working, '09:00', 'S-1-5-21-123')
        for mutation in ('exec', 'logon', 'privilege', 'principal', 'context'):
            with self.subTest(mutation=mutation):
                root = ET.fromstring(original)
                if mutation == 'exec':
                    ET.SubElement(root.find('t:Actions', NS), '{%s}Exec' % scheduler._NS)
                elif mutation == 'logon':
                    ET.SubElement(root.find('t:Triggers', NS), '{%s}LogonTrigger' % scheduler._NS)
                elif mutation == 'privilege':
                    root.find('t:Principals/t:Principal/t:RunLevel', NS).text = 'HighestAvailable'
                elif mutation == 'principal':
                    ET.SubElement(root.find('t:Principals', NS), '{%s}Principal' % scheduler._NS)
                else:
                    root.find('t:Actions', NS).set('Context', 'Other')
                changed = ET.tostring(root, encoding='unicode')
                calls = []
                def runner(args, **kwargs):
                    calls.append(args)
                    return subprocess.CompletedProcess(args, 0, changed, '')
                controller = scheduler.SchedulerController(working, command, arguments, working,
                    user_sid='S-1-5-21-123', runner=runner)
                self.assertFalse(controller.status(True, '09:00'))
                with self.assertRaises(scheduler.SchedulerError):
                    controller.sync(True, '09:00')
                with self.assertRaises(scheduler.SchedulerError):
                    controller.sync(False, '09:00')
                self.assertTrue(all('/query' in args for args in calls))

    def test_system_tools_do_not_use_current_directory_or_path(self):
        captured = []
        def runner(args, **kwargs):
            captured.append(args)
            return subprocess.CompletedProcess(args, 0, b'', b'')
        with patch.object(scheduler, '_system_directory', return_value=self.root / 'System32', create=True):
            for name in ('schtasks.exe', 'whoami.exe', 'powershell.exe'):
                scheduler._run([name, '/test'], runner)
        expected = [self.root / 'System32' / 'schtasks.exe',
                    self.root / 'System32' / 'whoami.exe',
                    self.root / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe']
        self.assertEqual([Path(args[0]) for args in captured], expected)

if __name__ == '__main__':
    unittest.main()
