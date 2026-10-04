import unittest
from pathlib import Path
from types import SimpleNamespace
from tests import test_browser_server as browser_fixture
from tests import test_search_jobs as jobs_fixture
from github_radar.search_jobs import SearchJobs
from github_radar.service import RadarService
from github_radar.storage import RadarStore

class SearchControlsTests(unittest.TestCase):
    setUp=browser_fixture.BrowserServerTests.setUp
    get=browser_fixture.BrowserServerTests.get
    post=browser_fixture.BrowserServerTests.post
    def test_settings_persist_and_require_session(self):
        self.assertEqual(self.get('/api/search/settings',token=False)[0],403)
        self.assertEqual(self.get('/api/search/settings')[0],200)
        self.assertEqual(self.post('/api/search/settings',{'enabled':False})[0],200)
        self.assertFalse(RadarStore(self.store.data_dir).load_search_enabled())
        for bad in ({'enabled':1},{'enabled':True,'url':'https://example.com'},{}):
            self.assertEqual(self.post('/api/search/settings',bad)[0],400)
    def test_disable_cancels_active_search_without_clearing_data(self):
        cancelled=[]
        self.service.search_jobs=SimpleNamespace(cancel=lambda:cancelled.append(True))
        self.assertEqual(self.post('/api/search/settings',{'enabled':False})[0],200)
        self.assertEqual(cancelled,[True])
    def test_cancel_and_status_are_session_protected(self):
        cancelled=[]
        self.service.search_jobs=SimpleNamespace(cancel=lambda:cancelled.append(True))
        self.assertEqual(self.post('/api/search/cancel',{},token=False)[0],403)
        self.assertEqual(self.post('/api/search/cancel',{'url':'bad'})[0],400)
        self.assertEqual(self.post('/api/search/cancel',{})[0],202)
        self.assertEqual(cancelled,[True])
        self.assertEqual(self.get('/api/search/status')[0],200)
    def test_cost_copy_matches_automatic_discovery_and_has_controls(self):
        assets=Path(__file__).resolve().parents[1]/'github_radar/web_assets'
        html=(assets/'index.html').read_text(encoding='utf-8')
        self.assertIn('id="search-ai-enabled"',html)
        self.assertIn('id="search-cancel"',html)
        self.assertNotIn('AI 仍由你点击后运行',html)
        self.assertNotIn('AI runs only when you click.',(assets/'i18n.js').read_text(encoding='utf-8'))

class SearchEnabledJobsTests(unittest.TestCase):
    setUp=jobs_fixture.SearchJobsTests.setUp
    engine=jobs_fixture.SearchJobsTests.engine
    def test_disabled_auto_uses_public_fallback_but_manual_continue_works(self):
        self.store.save_search_enabled(False)
        jobs=SearchJobs(RadarService(SimpleNamespace(),self.store),self.store,self.engine,now=lambda:self.now)
        self.assertIsNone(jobs.refresh(self.now.date().isoformat(),self.now.isoformat()))
        self.assertEqual(self.calls,[])
        jobs.continue_keyword(self.a.id,self.now.date().isoformat())
        self.assertEqual(len(self.calls),1)
    def test_disable_during_module_does_not_start_next_paid_module(self):
        def factory():
            engine=self.engine(); original=engine.run
            def run(scope,**kwargs):
                value=original(scope,**kwargs);self.store.save_search_enabled(False);return value
            engine.run=run;return engine
        jobs=SearchJobs(RadarService(SimpleNamespace(),self.store),self.store,factory,now=lambda:self.now)
        result=jobs.refresh(self.now.date().isoformat(),self.now.isoformat())
        self.assertEqual(len(self.calls),1)
        self.assertIn('部分',result.message)
