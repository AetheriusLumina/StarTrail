"""Real Windows byte locks bridge Inno maintenance and frozen entrypoints."""
import ctypes
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from github_radar.__main__ import main

@contextmanager
def installer_lock(root):
    """Use the same native LockFile call as Inno, not a mock guard."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p,
        ctypes.c_ulong,ctypes.c_ulong,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.LockFile.argtypes=[ctypes.c_void_p]+[ctypes.c_ulong]*4
    kernel.UnlockFile.argtypes=[ctypes.c_void_p]+[ctypes.c_ulong]*4
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    path=root/'AppFiles/.radar-maintenance.lock'
    handle=kernel.CreateFileW(str(path),0xC0000000,7,None,4,0x80,None)
    assert handle!=ctypes.c_void_p(-1).value
    locked=bool(kernel.LockFile(handle,0,0,1,0))
    try:yield locked
    finally:
        if locked:kernel.UnlockFile(handle,0,0,1,0)
        kernel.CloseHandle(handle)

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='Radar 维护 ')
        self.addCleanup(self.temp.cleanup)
        self.root=(Path(self.temp.name)/'中文 安装').resolve()
        (self.root/'AppFiles').mkdir(parents=True)
        self.data=self.root/'UserData'

    def test_installer_reservation_blocks_all_initialization_and_task_reregistration(self):
        actions=([],['--scheduled-refresh'],['--refresh'],['--sync-scheduler'],['--add-keyword','python'])
        with installer_lock(self.root) as locked:
            self.assertTrue(locked)
            with (patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'GitHubRadar.exe')),
                patch('github_radar.storage.RadarStore',side_effect=AssertionError('database initialized during maintenance')) as store,
                patch('github_radar.windows_scheduler.SchedulerController') as scheduler,
                patch('ctypes.windll.user32.MessageBoxW')):
                for action in actions:
                    with self.subTest(action=action):
                        self.assertEqual(main(['--data-dir',str(self.data)]+action),1)
                store.assert_not_called();scheduler.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_delayed_initialization_reserves_runtime_before_database_or_update_lock(self):
        from github_radar.storage import RadarStore
        entered=threading.Event();release=threading.Event();errors=[]
        def delayed_store(*args,**kwargs):
            entered.set()
            if not release.wait(3):raise AssertionError('test release missing')
            return RadarStore(*args,**kwargs)
        def run():
            try:self.assertEqual(main(['--data-dir',str(self.data),'--scheduled-refresh']),0)
            except BaseException as e:errors.append(e)
        with (patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'GitHubRadar.exe')),
            patch('github_radar.storage.RadarStore',side_effect=delayed_store),
            patch('github_radar.browser_launcher.handoff_scheduled_refresh',return_value=None),
            patch('github_radar.daily_update.run_scheduled_update',return_value='success')):
            thread=threading.Thread(target=run);thread.start()
            try:
                self.assertTrue(entered.wait(3))
                with installer_lock(self.root) as locked:
                    self.assertFalse(locked,'Installer must fail safely while a startup is already admitted')
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        with installer_lock(self.root) as locked:self.assertTrue(locked)

    def test_installer_helper_can_clear_task_again_under_held_reservation(self):
        with installer_lock(self.root) as locked:
            self.assertTrue(locked)
            with (patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'GitHubRadar.exe')),
                patch('github_radar.uninstall.prepare_uninstall') as prepare):
                self.assertEqual(main(['--data-dir',str(self.data),'--prepare-uninstall']),0)
                prepare.assert_called_once_with(self.root,self.data)

    def test_runtime_reservation_is_shared_and_released_on_failure(self):
        from github_radar.maintenance import runtime_access
        with self.assertRaisesRegex(RuntimeError,'owned failure'):
            with runtime_access(self.root):
                with runtime_access(self.root):
                    with installer_lock(self.root) as locked:self.assertFalse(locked)
                raise RuntimeError('owned failure')
        with installer_lock(self.root) as locked:self.assertTrue(locked)

if __name__=='__main__':unittest.main()
