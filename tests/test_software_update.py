import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from github_radar.software_update import ReleaseClient,SoftwareUpdateError,download_installer,newer_version

BASE='https://github.com/AetheriusLumina/StarTrail/releases/download/'
class Response(io.BytesIO):
    def __init__(self,data,url):super().__init__(data);self.url=url;self.headers={}
    def geturl(self):return self.url
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

class SoftwareUpdateTests(unittest.TestCase):
    def release(self,tag='v0.5.0-preview.2',preview=True):
        return {'tag_name':tag,'draft':False,'prerelease':preview,'html_url':'https://github.com/AetheriusLumina/StarTrail/releases/tag/'+tag,'body':'New features',
            'assets':[{'name':'StarTrail_Setup.exe','size':8,'browser_download_url':BASE+tag+'/StarTrail_Setup.exe'},
                      {'name':'SHA256SUMS.txt','size':100,'browser_download_url':BASE+tag+'/SHA256SUMS.txt'}]}
    def client(self,releases,data=b'installer'):
        checksum=hashlib.sha256(data).hexdigest()
        def open_(request,timeout):
            url=request.full_url
            content=json.dumps(releases).encode() if 'api.github.com' in url else (checksum+'  StarTrail_Setup.exe\n').encode()
            return Response(content,url)
        return ReleaseClient(open_)
    def test_only_releases_with_installer_and_hash_are_updates(self):
        absent=self.release();absent['assets']=[]
        chosen=self.client([absent,self.release()]).check('v0.5.0-preview.1','preview')
        self.assertEqual(chosen.tag,'v0.5.0-preview.2')
        self.assertEqual(chosen.sha256,hashlib.sha256(b'installer').hexdigest())
    def test_stable_excludes_preview_and_draft(self):
        draft=self.release('v9.0.0',False);draft['draft']=True
        result=self.client([self.release(),draft,self.release('v0.5.0',False)]).check('v0.4.0','stable')
        self.assertEqual(result.tag,'v0.5.0')
    def test_bad_host_or_digest_is_rejected(self):
        bad=self.release();bad['assets'][0]['browser_download_url']='https://evil.test/installer.exe'
        with self.assertRaises(SoftwareUpdateError):self.client([bad]).check('v0.4.0','preview')
        bad=self.release();bad['assets'][0]['digest']='sha256:'+'0'*64
        with self.assertRaises(SoftwareUpdateError):self.client([bad]).check('v0.4.0','preview')
    def test_hash_failure_never_leaves_executable_ready(self):
        value=self.client([self.release()]).check('v0.4.0','preview')
        with tempfile.TemporaryDirectory() as tmp:
            def open_(request,timeout):return Response(b'corrupted',request.full_url)
            with self.assertRaises(SoftwareUpdateError):download_installer(value,Path(tmp),opener=open_)
            self.assertFalse(list(Path(tmp).glob('*.exe')))
    def test_verified_download_is_reused_without_network(self):
        value=self.client([self.release()]).check('v0.4.0','preview')
        value=__import__('dataclasses').replace(value,size=len(b'installer'))
        with tempfile.TemporaryDirectory() as tmp:
            path=download_installer(value,Path(tmp),opener=lambda req,timeout:Response(b'installer',req.full_url))
            self.assertEqual(path.read_bytes(),b'installer')
            self.assertEqual(download_installer(value,Path(tmp),opener=lambda *a,**k:self.fail('cached download')),path)
    def test_semver_preview_order_and_errors(self):
        self.assertTrue(newer_version('v0.5.0','v0.5.0-preview.9'))
        self.assertTrue(newer_version('v0.5.0-preview.10','v0.5.0-preview.2'))
        self.assertFalse(newer_version('v0.5.0-preview.2','v0.5.0'))
        with self.assertRaises(SoftwareUpdateError):newer_version('../../anything','v0.5.0')

    def test_workflow_crlf_checksum_is_accepted(self):
        release=self.release();digest=hashlib.sha256(b'installer').hexdigest()
        def open_(request,timeout):
            url=request.full_url
            return Response(json.dumps([release]).encode() if 'api.github.com' in url else (digest+'  StarTrail_Setup.exe\r\n').encode(),url)
        self.assertEqual(ReleaseClient(open_).check('v0.4.0','preview').sha256,digest)
