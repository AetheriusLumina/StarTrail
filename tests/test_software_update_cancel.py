"""Real cache cancellation with synthetic data; installers never executed."""
import hashlib, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch
from github_radar.software_update import SoftwareUpdater, SoftwareRelease, SoftwareUpdateError

class CancelTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.data=Path(folder.name)/'UserData';self.cache=self.data/'.software-updates';self.cache.mkdir(parents=True)
        self.tag='v0.5.0-preview.4'; self.path=self.cache/(self.tag+'-StarTrail_Setup.exe');self.path.write_bytes(b'installer')
        self.release=SoftwareRelease(self.tag,'','', 'https://github.com/AetheriusLumina/StarTrail/releases/download/'+self.tag+'/StarTrail_Setup.exe',hashlib.sha256(b'installer').hexdigest(),9,True)
        self.updater=SoftwareUpdater(self.data);self.updater.release=self.release
        self.keep=self.data/'history-fixture.txt';self.keep.write_text('keep history')
        self.backup=self.data/'Backups';self.backup.mkdir();(self.backup/'keep.txt').write_text('backup')
        self.other=self.cache/'v0.4.0-StarTrail_Setup.exe';self.other.write_bytes(b'other version')
    def fail_launch(self):
        def fail(*args):raise SoftwareUpdateError('无法打开升级程序，软件继续运行')
        with patch('github_radar.software_update.sys.frozen',True,create=True):self.updater.start(fail)
        self.updater._worker.join(3);self.assertFalse(self.updater._worker.is_alive())
        self.assertEqual(self.updater.state()['status'],'error')
    def test_new_attempt_resets_old_progress_before_download(self):
        self.updater._set(downloaded=9,total=9)
        started=threading.Event();finish=threading.Event()
        def wait(*args,**kwargs):started.set();finish.wait(3);return self.path
        with patch('github_radar.software_update.sys.frozen',True,create=True),patch('github_radar.software_update.download_installer',wait):
            self.updater.start(lambda *a:None);self.assertTrue(started.wait(2))
            self.assertEqual((self.updater.state()['downloaded'],self.updater.state()['total']),(0,0))
            self.updater.cancel();finish.set();self.updater._worker.join(3)

    def test_failed_update_cancel_deletes_only_its_cached_installer(self):
        self.fail_launch();self.assertTrue(self.path.exists())
        self.assertIn('无法打开',self.updater.state()['message'])
        result=self.updater.cancel()
        self.assertEqual(result['status'],'cancelled')
        self.assertFalse(self.path.exists());self.assertTrue(self.other.exists());self.assertTrue(self.keep.exists());self.assertTrue((self.backup/'keep.txt').exists())
        self.assertEqual(result['downloaded'],0)
    def test_cleanup_permission_error_is_not_reported_as_success(self):
        self.fail_launch()
        with patch.object(Path,'unlink',side_effect=PermissionError('locked')):result=self.updater.cancel()
        self.assertEqual(result['status'],'error');self.assertIn('清理',result['message']);self.assertTrue(self.path.exists())
    def test_cancel_during_download_never_runs_ready_callback(self):
        entered=threading.Event();release=threading.Event();ready=[]
        def bounded_download(value,root,**kw):
            entered.set();release.wait(3);return self.path
        with patch('github_radar.software_update.sys.frozen',True,create=True),patch('github_radar.software_update.download_installer',bounded_download):
            self.updater.start(lambda *a:ready.append(a));self.assertTrue(entered.wait(2))
            self.updater.cancel();release.set();self.updater._worker.join(3)
        self.assertFalse(ready);self.assertEqual(self.updater.state()['status'],'cancelled');self.assertFalse(self.path.exists())
    def test_cleanup_refuses_a_redirected_cache_directory(self):
        self.fail_launch()
        outside=self.data.parent/'outside';self.cache.rename(outside)
        try:self.cache.symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Directory symlinks require local privileges')
        result=self.updater.cancel();self.assertEqual(result['status'],'error');self.assertTrue((outside/self.path.name).exists())
