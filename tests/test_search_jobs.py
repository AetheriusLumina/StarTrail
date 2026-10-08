import tempfile
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from github_radar.storage import RadarStore
from github_radar.service import RadarService
from github_radar.search_jobs import SearchJobs
from github_radar.search_types import SearchProgress

class SearchJobsTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=RadarStore(t.name)
        self.a=self.store.add_keyword('a');self.b=self.store.add_keyword('b')
        self.now=datetime.now().astimezone();self.calls=[]
    def engine(self):
        def run(scope,**kw):
            self.calls.append((scope.section,scope.keyword_id,scope.stat_date))
            return SearchProgress('job',scope.section,scope.keyword_id,scope.local_date,status='done')
        return SimpleNamespace(run=run,provider=SimpleNamespace(cancel=lambda:None))
    def test_long_update_keeps_deferred_module_and_global_leases_alive(self):
        from datetime import timedelta
        from github_radar.search_storage import SearchStore
        from github_radar.search_types import SearchScope
        search=SearchStore(self.store)
        scope=SearchScope('growth',None,self.now.date().isoformat(),None,'',100,None)
        global_lease=search.claim_run(scope,'test-owner',namespace='refresh',now=self.now.timestamp(),ttl=1900)
        keyword=SearchScope('keyword',self.a.id,scope.local_date,None,'a',100,None)
        module_lease=search.claim_run(keyword,'test-module',now=self.now.timestamp(),ttl=1900)
        jobs=SearchJobs(RadarService(SimpleNamespace(),self.store),self.store,self.engine,now=lambda:self.now)
        jobs._engines=[SimpleNamespace(current_lease=module_lease)]
        self.now+=timedelta(seconds=1800)
        self.assertTrue(jobs._renew_leases(search,global_lease))
        self.now+=timedelta(seconds=1800)
        self.assertTrue(search.renew_run(global_lease,now=self.now.timestamp()))
        self.assertTrue(search.renew_run(module_lease,now=self.now.timestamp()))

    def test_manual_continue_uses_same_coordinator_and_extra_bounded_batch(self):
        service=RadarService(SimpleNamespace(),self.store);extra=[]
        def engine():
            def run(scope,**kw):
                self.assertTrue(jobs.foreground_active)
                progress=SearchProgress('job','keyword',scope.keyword_id,scope.local_date,status='running')
                kw['on_progress'](progress)
                self.assertEqual(jobs.foreground_progress,[progress])
                extra.append((scope,kw));return SearchProgress('job','keyword',scope.keyword_id,scope.local_date,status='partial')
            return SimpleNamespace(run=run,provider=SimpleNamespace(cancel=lambda:None))
        jobs=SearchJobs(service,self.store,engine,now=lambda:self.now)
        result=jobs.continue_keyword(self.a.id,self.now.date().isoformat(),model_id='chosen')
        self.assertEqual(result.status,'partial');self.assertEqual(len(extra),1)
        self.assertEqual(extra[0][0].model_id,'chosen');self.assertTrue(extra[0][1]['continue_search'])
        self.assertEqual(extra[0][1]['max_new'],200)
        self.assertFalse(jobs.foreground_active)
        self.assertEqual(jobs.foreground_progress,[])

    def test_failed_continuation_invalidates_old_quota_success(self):
        from contextlib import closing
        import json
        from github_radar.search_storage import SearchStore
        jobs=SearchJobs(RadarService(SimpleNamespace(),self.store),self.store,self.engine,ready=lambda:False,now=lambda:self.now)
        with closing(self.store._connect()) as db,db:
            db.execute("INSERT INTO settings(name,value) VALUES('search_refresh_timing',?)",(json.dumps({'status':'ok','quota_scoped':True,'published_at':'old'}),))
        with self.assertRaises(ValueError):jobs.continue_keyword(self.a.id,self.now.date().isoformat())
        timing=SearchStore(self.store).refresh_timing()
        self.assertEqual(timing['status'],'error')
        self.assertFalse(timing['quota_scoped'])
        self.assertIsNone(timing['published_at'])
        self.assertFalse(jobs.foreground_active)

    def test_growth_then_keywords_all_automatic_and_yesterday_utc(self):
        service=RadarService(SimpleNamespace(),self.store)
        jobs=SearchJobs(service,self.store,self.engine,ready=lambda:True,now=lambda:self.now)
        result=jobs.refresh(self.now.date().isoformat(),self.now.isoformat())
        self.assertEqual([c[:2] for c in self.calls],[('growth',None),('keyword',self.a.id),('keyword',self.b.id)])
        self.assertEqual(result.status,'ok')
    def test_busy_operation_reuses_no_second_ai(self):
        from github_radar.search_storage import SearchStore
        from github_radar.search_types import SearchScope
        from datetime import timezone,timedelta
        service=RadarService(SimpleNamespace(),self.store)
        jobs=SearchJobs(service,self.store,self.engine,ready=lambda:True,now=lambda:self.now)
        scope=SearchScope('growth',None,self.now.date().isoformat(),(self.now.astimezone(timezone.utc).date()-timedelta(days=1)).isoformat(),'',100,None)
        SearchStore(self.store).claim_run(scope,'other',namespace='refresh',now=self.now.timestamp())
        result=jobs.refresh(scope.local_date,self.now.isoformat())
        self.assertEqual(self.calls,[]);self.assertIn('进行',result.message)
    def test_disconnected_does_not_call_ai_or_fake_success(self):
        service=RadarService(SimpleNamespace(),self.store)
        jobs=SearchJobs(service,self.store,self.engine,ready=lambda:False,now=lambda:self.now)
        self.assertIsNone(jobs.refresh(self.now.date().isoformat(),self.now.isoformat()))
        self.assertEqual(self.calls,[])
