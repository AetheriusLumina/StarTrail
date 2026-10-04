import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from github_radar.software_update import SoftwareRelease,SoftwareUpdateError,launch_installer

class InstallerLaunchTests(unittest.TestCase):
    def test_only_reverified_download_is_launched_for_this_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=root/'UserData';cache=data/'.software-updates';cache.mkdir(parents=True)
            exe=root/'GitHubRadar.exe';exe.write_bytes(b'app')
            (data/'.github-radar-data').write_text('data')
            tag='v0.5.0-preview.2';package=cache/(tag+'-StarTrail_Setup.exe');package.write_bytes(b'installer')
            release=SoftwareRelease(tag,'','', 'https://github.com/AetheriusLumina/StarTrail/releases/download/'+tag+'/StarTrail_Setup.exe',hashlib.sha256(b'installer').hexdigest(),9,True)
            with patch('github_radar.software_update.sys.frozen',True,create=True),patch('github_radar.software_update.sys.executable',str(exe)),patch('github_radar.software_update.subprocess.Popen') as popen:
                launch_installer(package,release,data)
                args=popen.call_args.args[0];self.assertEqual(args,[str(package.resolve()),'/DIR='+str(root.resolve())])
                self.assertFalse(popen.call_args.kwargs.get('shell',False))
                package.write_bytes(b'tampered!')
                with self.assertRaises(SoftwareUpdateError):launch_installer(package,release,data)
                self.assertEqual(popen.call_count,1)
    def test_source_mode_and_foreign_data_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(SoftwareUpdateError):launch_installer(Path(folder)/'unknown.exe',None,folder)
