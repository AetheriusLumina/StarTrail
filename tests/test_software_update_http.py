"""Loopback upgrade admission and quit behavior without live downloads."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from github_radar.browser_server import BrowserServer
from github_radar.storage import RadarStore
from github_radar.service import RadarService
from tests.test_browser_server import NoNetworkClient

class Updater:
    def __init__(self):self.calls=[];self.callback=None;self.closed=False
    def state(self):return {'status':'available','current':'v0.5.0-preview.1','release':{'tag':'v0.5.0-preview.2'},'installable':True}
    def check(self,force=False):self.calls.append(force);return self.state()
    def start(self,callback):self.callback=callback;return {'status':'downloading'}
    def cancel(self):self.calls.append('cancel');return {'status':'cancelled'}
    def close(self):self.closed=True

class UpgradeHTTPTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=RadarStore(Path(temp.name)/'UserData');self.updater=Updater()
        self.server=BrowserServer(RadarService(NoNetworkClient(),self.store),self.store,software_updater=self.updater)
        self.server.start();self.addCleanup(self.server.close)
        self.base=self.server.url.split('/#')[0]
    def request(self,path,payload=None,authorized=True):
        headers={'X-Radar-Token':self.server.token,'Origin':self.base} if authorized else {}
        data=None if payload is None else json.dumps(payload).encode()
        request=Request(self.base+path,headers=headers,data=data)
        try:
            with urlopen(request,timeout=3) as response:return response.status,json.loads(response.read())
        except HTTPError as error:return error.code,json.loads(error.read())
    def test_startup_status_and_check_are_local_authenticated_actions(self):
        self.assertEqual(self.updater.calls,[False])
        self.assertEqual(self.request('/api/software-update')[0],200)
        self.assertEqual(self.request('/api/software-update',authorized=False)[0],403)
        self.assertEqual(self.request('/api/software-update/check',{},False)[0],403)
        self.assertEqual(self.request('/api/software-update/check',{})[0],202)
        self.assertTrue(self.updater.calls[-1])
    def test_install_requires_explicit_confirmation_and_idle_data_tasks(self):
        self.assertEqual(self.request('/api/software-update/install',{})[0],400)
        self.assertEqual(self.request('/api/software-update/install',{'confirmed':True,'url':'https://other.test'})[0],400)
        gate=threading.Event();self.server._worker=threading.Thread(target=gate.wait)
        self.server._worker.start()
        try:self.assertEqual(self.request('/api/software-update/install',{'confirmed':True})[0],409)
        finally:gate.set();self.server._worker.join()
        self.assertEqual(self.request('/api/software-update/install',{'confirmed':True})[0],202)
        self.assertIsNotNone(self.updater.callback)
    def test_cancel_is_authenticated_and_never_accepts_arbitrary_paths(self):
        self.assertEqual(self.request('/api/software-update/cancel',{},False)[0],403)
        self.assertEqual(self.request('/api/software-update/cancel',{'path':'elsewhere'})[0],400)
        self.assertEqual(self.request('/api/software-update/cancel',{})[1]['status'],'cancelled')
        self.assertEqual(self.updater.calls[-1],'cancel')
        self.server._quitting=True
        self.assertEqual(self.request('/api/software-update/cancel',{})[0],409)
    def test_closing_cancels_updater(self):
        self.server.close();self.assertTrue(self.updater.closed)
