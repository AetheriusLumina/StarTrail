"""Integration checks use real storage and public client with isolated data."""
import json
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch
from github_radar.storage import RadarStore
from github_radar.models import Recommendation
from github_radar.search_types import SearchScope, ObservedRepository, SearchProgress
from github_radar.search_sources import SearchSources
from github_radar.search_storage import SearchStore
from github_radar.search_coordinator import SearchCoordinator
from github_radar.search_jobs import SearchJobs
from github_radar.github_client import GitHubClient
from github_radar.discovery_types import RequestBudget, SearchPage
from github_radar.service import RadarService
from github_radar.browser_server import BrowserServer
from github_radar.windows_scheduler import build_task_xml, _same_schedule, TaskState
from tests.test_storage import repository
from tests.test_search_coordinator import FakeAI

class UpdateRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.store=RadarStore(temp.name)
        self.now=datetime.now().astimezone()
        self.day=self.now.date().isoformat()

    def test_real_growth_sources_collect_without_missing_store_method(self):
        calls=[]
        class Client:
            def get_repository(inner,name):calls.append(name);return repository(1,3000)
            def search_page(inner,*a,**kw):return SearchPage((),0,False)
        repo=repository(1,3000)
        self.store.commit_daily('2026-10-04',[repo],[],[Recommendation(1,'2026-10-04','growth',None,20,None,'2026-10-04T12:00:00Z')])
        scope=SearchScope('growth',None,'2026-10-05','2026-10-04','',100,None)
        sources=SearchSources(Client(),self.store,None,None,lambda:0)
        sources.collect(scope,None,None,RequestBudget(100,10,1000),threading.Event(),lambda *a:None)
        self.assertEqual(calls,[repo.full_name])
        self.assertEqual(len(SearchStore(self.store).candidate_page(scope,None)),1)

    def test_recent_growth_repositories_are_bounded_and_not_keywords(self):
        repos=[repository(i,1000+i) for i in range(1,5)]
        self.store.commit_daily('2026-10-03',repos,[],[Recommendation(1,'2026-10-03','growth',None,2,None,'2026-10-03T12:00:00Z')])
        self.store.commit_daily('2026-10-04',repos,[],[Recommendation(2,'2026-10-04','growth',None,3,None,'2026-10-04T12:00:00Z'),Recommendation(3,'2026-10-04','keyword',1,None,None,'2026-10-04T12:00:00Z')])
        self.store.commit_daily('2026-10-06',repos,[],[Recommendation(4,'2026-10-06','growth',None,4,None,'2026-10-06T12:00:00Z')])
        self.assertEqual([r.id for r in self.store.recent_growth_repositories('2026-10-05',limit=200)],[2])
        self.assertEqual(self.store.recent_growth_repositories('2026-10-02'),[])
        self.assertEqual(len(self.store.recent_growth_repositories('2026-10-05',limit=1)),1)

    def test_expired_quotas_are_renewed_before_new_search_budget(self):
        rule=self.store.add_keyword('skills',1000)
        scope=SearchScope('keyword',rule.id,self.day,None,'skills',1000,'model')
        client=GitHubClient();client.core_remaining=client.search_remaining=0
        client.core_reset_at=client.search_reset_at=self.now.timestamp()-1
        source=SimpleNamespace(collected=0,notes=[],limited=False,trendshift=None)
        def collect(scope,expansion,result,budget,event,progress):
            self.assertTrue(budget.can_spend('core',1,0))
            self.assertTrue(budget.can_spend('search',1,0))
        source.collect=collect
        result=SearchCoordinator(self.store,client,FakeAI(),sources=source,now=lambda:self.now,clock=lambda:0).run(scope)
        self.assertEqual(result.status,'done',result.notes)
        self.assertIsNone(client.core_remaining)

    def test_active_quota_reset_is_not_ignored(self):
        rule=self.store.add_keyword('skills',1000)
        scope=SearchScope('keyword',rule.id,self.day,None,'skills',1000,'model')
        client=GitHubClient();client.core_remaining=client.search_remaining=0
        client.core_reset_at=client.search_reset_at=self.now.timestamp()+3600
        source=SimpleNamespace(collected=0,notes=[],limited=False,trendshift=None)
        def collect(scope,expansion,result,budget,event,progress):
            self.assertFalse(budget.can_spend('core',1,0));self.assertFalse(budget.can_spend('search',1,0))
        source.collect=collect
        SearchCoordinator(self.store,client,FakeAI(),sources=source,now=lambda:self.now,clock=lambda:0).run(scope)
        self.assertEqual(client.core_remaining,0)

    def test_windows_retry_triggers_continue_after_sleep_until_day_end(self):
        ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        root=ET.fromstring(build_task_xml(self.store.data_dir/'app.exe',(),self.store.data_dir,'09:00','S-1-5-21-1',date(2026,10,5)))
        triggers=root.findall('t:Triggers/t:CalendarTrigger',ns)
        self.assertEqual(len(triggers),1)
        self.assertEqual(triggers[0].findtext('t:Repetition/t:Interval',namespaces=ns),'PT1H')
        self.assertEqual(triggers[0].findtext('t:Repetition/t:Duration',namespaces=ns),'PT900M')
        self.assertEqual(root.findtext('t:Settings/t:WakeToRun',namespaces=ns),'false')

    def test_open_service_checks_after_resume_without_browser_request(self):
        service=RadarService(SimpleNamespace(),self.store)
        server=BrowserServer(service,self.store)
        calls=threading.Event()
        server.AUTO_UPDATE_POLL_SECONDS=0.01
        server.start_due_update=lambda:calls.set()
        try:
            server.start()
            self.assertTrue(calls.wait(1),'Open service did not check a due resumed update')
            server.close()
            self.assertFalse(server._schedule_thread.is_alive())
        finally:server.close()

    def test_manual_and_scheduled_errors_expose_and_persist_reason(self):
        service=RadarService(SimpleNamespace(),self.store)
        service.refresh=lambda *a:replace(service.load_latest(self.day),status='error',message='检索未完成',notes=('GitHub 请求额度已用完，请稍后再试',))
        server=BrowserServer(service,self.store)
        try:
            server._refresh_worker()
            failure=self.store.latest_refresh_failure()
            self.assertIsNotNone(failure)
            self.assertIn('GitHub 请求额度',failure[1])
            payload=server._issue_payload()
            self.assertEqual(payload['update_failure']['reason'],failure[1])
        finally:server.close()

    def test_search_failure_result_keeps_underlying_reason(self):
        service=RadarService(SimpleNamespace(),self.store)
        def factory():return SimpleNamespace(run=lambda scope,**kw:SearchProgress('job',scope.section,scope.keyword_id,self.day,status='paused',notes=('连接 GitHub 失败，请检查网络',)),provider=SimpleNamespace(cancel=lambda:None))
        result=SearchJobs(service,self.store,factory,now=lambda:self.now).refresh(self.day,self.now.isoformat())
        self.assertIn('连接 GitHub 失败',result.message)

    def test_partial_module_publish_can_retry_then_full_manual_success_stops_retry(self):
        from github_radar.daily_update import run_scheduled_update, auto_update_due
        from datetime import timedelta
        rule=self.store.add_keyword('skills',1000)
        service=RadarService(SimpleNamespace(),self.store)
        keyword_ready=[False]
        at=self.now.replace(hour=14,minute=0,second=0,microsecond=0)
        ticks=[at]
        def factory():
            def run(scope,**kw):
                if scope.section=='growth' or keyword_ready[0]:
                    self.store.commit_daily(self.day,[],[],[],completed_at=ticks[0].isoformat())
                    return SearchProgress('job',scope.section,scope.keyword_id,self.day,status='done')
                return SearchProgress('job',scope.section,scope.keyword_id,self.day,status='paused',notes=('连接 GitHub 失败',))
            return SimpleNamespace(run=run,provider=SimpleNamespace(cancel=lambda:None))
        service.search_jobs=SearchJobs(service,self.store,factory,now=lambda:ticks[0])
        self.assertEqual(run_scheduled_update(self.store,service,ticks[0]),'error')
        self.assertTrue(self.store.has_incomplete_search(self.day))
        self.assertEqual(self.store.latest_successful_date(),self.day)
        ticks[0]+=timedelta(hours=1)
        self.assertEqual(run_scheduled_update(self.store,service,ticks[0]),'error')
        self.assertEqual(self.store.auto_attempts(self.day).attempts,2)
        keyword_ready[0]=True
        self.assertEqual(service.refresh(self.day,ticks[0].isoformat()).status,'ok')
        self.assertFalse(self.store.has_incomplete_search(self.day))
        ticks[0]+=timedelta(hours=1)
        self.assertEqual(run_scheduled_update(self.store,service,ticks[0]),'skipped')
        self.assertEqual(self.store.auto_attempts(self.day).attempts,2)

    def test_budget_stop_has_a_user_visible_reason_without_faking_empty_success(self):
        rule=self.store.add_keyword('skills',1000)
        scope=SearchScope('keyword',rule.id,self.day,None,'skills',1000,'model')
        client=SimpleNamespace(search_page=lambda *a,**kw:self.fail('must not call exhausted GitHub quota'))
        source=SearchSources(client,self.store,None,None,lambda:0)
        source.collect(scope,None,None,RequestBudget(0,0,1000),threading.Event(),lambda *a:None)
        self.assertTrue(source.limited)
        self.assertTrue(any('额度' in note for note in source.notes),source.notes)

    def test_successor_marker_survives_previous_run_lease_release(self):
        service=RadarService(SimpleNamespace(),self.store)
        def factory():return SimpleNamespace(run=lambda scope,**kw:SearchProgress('job',scope.section,scope.keyword_id,self.day,status='done'),provider=SimpleNamespace(cancel=lambda:None))
        jobs=SearchJobs(service,self.store,factory,now=lambda:self.now)
        finish=SearchStore.finish_run
        def release_and_start_successor(search,lease):
            finish(search,lease)
            self.store.mark_search_incomplete(self.day,True)
        with patch.object(SearchStore,'finish_run',release_and_start_successor):
            jobs.refresh(self.day,self.now.isoformat())
        self.assertTrue(self.store.has_incomplete_search(self.day))

    def test_expired_run_cannot_clear_successor_marker_at_utc_boundary(self):
        from github_radar.search_types import SearchScope
        search=SearchStore(self.store)
        a=SearchScope('growth',None,self.day,'2026-10-03','',100,None)
        b=replace(a,stat_date='2026-10-04')
        first=search.claim_run(a,'first',namespace='refresh',now=100,ttl=10)
        self.store.mark_search_incomplete(self.day,True,lease=first,now=100)
        next_run=search.claim_run(b,'next',namespace='refresh',now=120,ttl=100)
        self.store.mark_search_incomplete(self.day,True,lease=next_run,now=120)
        self.assertFalse(self.store.mark_search_incomplete(self.day,False,lease=first,now=121))
        self.assertTrue(self.store.has_incomplete_search(self.day))

    def test_windows_normalized_midnight_repetition_is_stable(self):
        command=self.store.data_dir/'app.exe'
        xml=build_task_xml(command,(),self.store.data_dir,'00:00','S-1-5-21-1',date(2026,10,5)).replace('PT1440M','P1D')
        self.assertTrue(_same_schedule(TaskState(True,xml=xml),self.store.data_dir,'00:00','S-1-5-21-1'))

    def test_real_client_sources_store_and_growth_publication_complete(self):
        import io
        from urllib.parse import urlsplit
        from datetime import timezone,timedelta
        from github_radar.search_types import GrowthAssessment
        utc=self.now.astimezone(timezone.utc)
        target=utc.date()-timedelta(days=1)
        sunday=target-timedelta(days=(target.weekday()+1)%7)
        week=int(datetime.combine(sunday,datetime.min.time(),timezone.utc).timestamp())
        raw=[{'id':i,'full_name':f'example/repo-{i}','html_url':f'https://github.com/example/repo-{i}','description':'Project','topics':[],'language':'Python','stargazers_count':1000+i,'archived':False} for i in range(1,6)]
        calls=[]
        class Response(io.BytesIO):
            headers={'x-ratelimit-resource':'core','x-ratelimit-remaining':'4999','x-ratelimit-reset':str(int(self.now.timestamp()+3600))}
        def opener(request,timeout):
            calls.append(request.full_url)
            if urlsplit(request.full_url).path=='/search/repositories':
                value={'items':raw,'total_count':5,'incomplete_results':False}
                response=Response(json.dumps(value).encode())
                response.headers={**response.headers,'x-ratelimit-resource':'search','x-ratelimit-remaining':'29'}
                return response
            self.assertIn('/stargazers/history',request.full_url)
            return Response(json.dumps([{'week':week,'days':[40]*7}]).encode())
        client=GitHubClient(opener=opener,clock=lambda:0)
        ai=FakeAI()
        ai.assess_growth_batch=lambda items,scope,**kw:tuple(GrowthAssessment(p.observation.repo.id,'supported','Official daily evidence') for p in items)
        scope=SearchScope('growth',None,self.day,target.isoformat(),'',100,'model')
        sources=SearchSources(client,self.store,None,None,lambda:0)
        result=SearchCoordinator(self.store,client,ai,sources=sources,now=lambda:self.now,clock=lambda:0).run(scope)
        self.assertEqual(result.status,'done',result.notes)
        self.assertEqual(result.newly_checked,0)
        self.assertEqual(result.judgment_calls,0)
        self.assertEqual(len(self.store.daily_recommendations(self.day)),5)
        self.assertEqual({r.star_delta for r in self.store.daily_recommendations(self.day)},{40})
        self.assertEqual(len(calls),6)
