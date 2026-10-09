import json,tempfile,threading,unittest
from datetime import datetime,timezone,timedelta
from dataclasses import replace
from github_radar.discovery_types import RequestBudget
from github_radar.search_storage import SearchStore
from github_radar.storage import RadarStore
from github_radar.search_types import SearchScope,Publication,ObservedRepository
from github_radar.models import Recommendation,StarSnapshot
from tests.test_storage import repository

class ActualQuotaTests(unittest.TestCase):
    def test_last_ten_github_requests_are_available_by_default(self):
        budget=RequestBudget(10,30,float('inf'))
        for remaining in range(10,0,-1):
            self.assertTrue(budget.can_spend('core',1,0),remaining)
            budget.spend('core',1,0)
        self.assertEqual(budget.core_remaining,0)
        self.assertFalse(budget.can_spend('core',1,0))

    def test_midnight_atomic_finish_keeps_started_day_and_real_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RadarStore(tmp);search=SearchStore(store);rule=store.add_keyword('skills',100)
            start=datetime.fromisoformat('2026-10-09T23:59:00+08:00');finish=start+timedelta(minutes=2)
            scope=SearchScope('keyword',rule.id,start.date().isoformat(),None,'skills',100,None)
            lease=search.claim_run(scope,'owner',now=start.timestamp())
            repo=repository(1);at=start.isoformat()
            value=Publication(scope,lease.generation,(ObservedRepository(repo,at),),(StarSnapshot(repo.id,scope.local_date,repo.stars,at),),(Recommendation(repo.id,scope.local_date,'keyword',rule.id,None,None,at),),None)
            search.publish_all((value,),(lease,),now=finish)
            saved=store.daily_recommendations(scope.local_date)
            self.assertEqual(len(saved),1)
            self.assertEqual(saved[0].observed_at,at)
            self.assertEqual(store.daily_recommendations(finish.date().isoformat()),[])
            self.assertEqual(store.daily_updated_at(scope.local_date),finish.isoformat())

    def test_midnight_finish_does_not_accept_metadata_from_before_started_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RadarStore(tmp);search=SearchStore(store);rule=store.add_keyword('skills',100)
            start=datetime.fromisoformat('2026-10-09T23:59:00+08:00');finish=start+timedelta(minutes=2)
            scope=SearchScope('keyword',rule.id,start.date().isoformat(),None,'skills',100,None);lease=search.claim_run(scope,'owner',now=start.timestamp())
            repo=repository(1);at='2026-10-08T23:00:00+08:00'
            value=Publication(scope,lease.generation,(ObservedRepository(repo,at),),(StarSnapshot(repo.id,scope.local_date,repo.stars,at),),(Recommendation(repo.id,scope.local_date,'keyword',rule.id,None,None,at),),None)
            with self.assertRaises(ValueError):search.publish_all((value,),(lease,),now=finish)
            self.assertEqual(store.daily_recommendations(scope.local_date),[])

    def test_failure_message_shows_real_cause_not_source_counts_or_quota(self):
        from github_radar.search_jobs import failure_summary
        from github_radar.search_types import SearchProgress
        p=SearchProgress('x','growth',None,'2026-10-09',status='paused',notes=('本轮请求额度已到预留线；未处理候选和检索断点保留，下次更新继续','实际读取 25 个项目；来源顺序与官方新增排名分别保存','磁盘写入失败'))
        self.assertEqual(failure_summary((p,)),'磁盘写入失败')

    def test_developer_quota_evidence_keeps_server_count_not_local_estimate(self):
        from github_radar.github_client import GitHubClient
        client=GitHubClient(opener=lambda *a,**kw:None,budget=RequestBudget(9,30,float('inf')))
        client._capture_limit({'X-RateLimit-Resource':'core','X-RateLimit-Remaining':'10','X-RateLimit-Limit':'5000','X-RateLimit-Reset':'1791600000'},'/repos/org/repo')
        self.assertEqual(client.rate_limit_observations['core']['remaining'],10)
        self.assertEqual(client.rate_limit_observations['core']['limit'],5000)
        self.assertEqual(client.core_remaining,9)

    def test_unknown_quota_without_server_headers_is_not_quota_success(self):
        from types import SimpleNamespace
        from github_radar.search_sources import SearchSources
        from github_radar.discovery_types import DiscoveryCandidate
        with tempfile.TemporaryDirectory() as tmp:
            store=RadarStore(tmp);source=SearchSources(SimpleNamespace(get_repository=lambda name:repository(1)),store,None,None,lambda:0)
            budget=RequestBudget(None,30,float('inf'))
            source.resolve_candidate(DiscoveryCandidate('owner/repo-1',1,('tracked',),'2026-10-09T23:59:00+08:00'),budget)
            source.resolve_candidate(DiscoveryCandidate('owner/repo-1',1,('tracked',),'2026-10-09T23:59:00+08:00'),budget)
            self.assertFalse(source.quota_limited)
            self.assertTrue(source.failed)

    def test_midnight_missing_repository_is_skipped_for_started_day(self):
        from types import SimpleNamespace
        from github_radar.search_sources import SearchSources
        from github_radar.github_client import GitHubRequestError
        with tempfile.TemporaryDirectory() as tmp:
            store=RadarStore(tmp);search=SearchStore(store)
            scope=SearchScope('growth',None,'2026-10-09','2026-10-08','',100,None)
            search.save_candidates(scope,(ObservedRepository(repository(1),'2026-10-08T22:00:00+08:00'),),())
            def missing(*args):raise GitHubRequestError('missing',status=404)
            source=SearchSources(SimpleNamespace(get_repository=missing,get_repository_by_id=missing),store,None,None,lambda:0)
            source.now=lambda:datetime.fromisoformat('2026-10-10T00:01:00+08:00')
            list(source.refresh_candidates_steps(scope,RequestBudget(5000,30,float('inf')),threading.Event(),lambda *a:None))
            self.assertFalse(source.failed)
            self.assertTrue(search.unavailable(1,scope.local_date))

    def test_failure_summary_prefers_origin_over_sibling_abort(self):
        from github_radar.search_jobs import failure_summary
        from github_radar.search_types import SearchProgress
        a=SearchProgress('a','growth',None,'2026-10-09',status='paused',notes=('磁盘写入失败',))
        b=SearchProgress('b','keyword',1,'2026-10-09',status='paused',notes=('另一榜单未完成，整份旧榜保留',))
        self.assertEqual(failure_summary((a,b)),'磁盘写入失败')

    def test_new_server_window_restores_available_quota(self):
        b=RequestBudget(1,30,float('inf'))
        b.observe({'X-RateLimit-Resource':'core','X-RateLimit-Remaining':'1','X-RateLimit-Reset':'100'})
        b.spend('core',1,0)
        b.observe({'X-RateLimit-Resource':'core','X-RateLimit-Remaining':'4999','X-RateLimit-Reset':'3700'})
        self.assertEqual(b.core_remaining,4999)
        b.observe({'X-RateLimit-Resource':'core','X-RateLimit-Remaining':'10','X-RateLimit-Reset':'100'})
        self.assertEqual(b.core_remaining,4999)

    def test_local_zero_is_reconciled_against_actual_server_quota(self):
        from github_radar.github_client import GitHubClient
        from tests.test_github_client import FakeResponse
        calls=[]
        def opener(request,timeout):
            calls.append(request.full_url)
            return FakeResponse({'resources':{'core':{'remaining':4984,'limit':5000,'reset':9999999999}}})
        c=GitHubClient(opener=opener);b=RequestBudget(0,30,float('inf'))
        b.quota_refresh=c.reconcile_quota
        self.assertTrue(b.can_spend('core',1,0))
        self.assertEqual(b.core_remaining,4984)
        self.assertEqual(len(calls),1)

    def test_confirmed_server_zero_stops_without_hour_wait(self):
        from github_radar.github_client import GitHubClient
        from tests.test_github_client import FakeResponse
        c=GitHubClient(opener=lambda *a,**kw:FakeResponse({'resources':{'core':{'remaining':0,'limit':5000,'reset':9999999999}}}))
        b=RequestBudget(0,30,float('inf'));b.quota_refresh=c.reconcile_quota
        self.assertFalse(b.can_spend('core',1,0))
        self.assertEqual(c.rate_limit_observations['core']['remaining'],0)

    def test_quota_refresh_does_not_hold_budget_lock(self):
        b=RequestBudget(0,30,float('inf'));available=threading.Event();workers=[]
        def refresh(budget,resource):
            def response():
                with budget._lock:available.set()
            worker=threading.Thread(target=response,daemon=True);workers.append(worker);worker.start()
            self.assertTrue(available.wait(1),'remote quota check held the budget lock')
            budget.core_remaining=1
        b.quota_refresh=refresh
        try:b.spend('core',1,0)
        finally:
            for worker in workers:worker.join(2)
        self.assertEqual(b.core_remaining,0)

    def test_quota_check_rate_limit_latches_whole_round(self):
        from github_radar.github_client import GitHubClient
        from urllib.error import HTTPError
        def limited(*a,**kw):raise HTTPError('https://api.github.com/rate_limit',429,'limited',{'Retry-After':'30'},None)
        c=GitHubClient(opener=limited);event=threading.Event()
        b=RequestBudget(0,30,float('inf'),quota_event=event);b.quota_refresh=c.reconcile_quota
        self.assertFalse(b.can_spend('core',1,0))
        self.assertTrue(event.is_set())
        self.assertEqual(c.last_rate_limit['status'],429)

    def test_actual_github_rate_limit_variant_is_quota_not_invalid_metadata(self):
        from github_radar.github_client import GitHubClient,GitHubRateLimitError
        from tests.test_github_client import FakeResponse
        body={'errors':[{'type':'RATE_LIMIT','code':'graphql_rate_limit','message':'API rate limit already exceeded for user ID [redacted].'}]}
        c=GitHubClient(opener=lambda *a,**kw:FakeResponse(body,{'X-RateLimit-Resource':'graphql','X-RateLimit-Remaining':'0','X-RateLimit-Reset':'9999999999'}),token_provider=lambda:'isolated-test')
        with self.assertRaises(GitHubRateLimitError):c.get_repositories_batch(('github/gitignore',))
        self.assertEqual(c.last_rate_limit['resource'],'graphql')
        self.assertEqual(c.last_rate_limit['status'],200)

    def test_rate_limit_variant_mixed_with_permission_error_is_failure(self):
        from github_radar.github_client import GitHubClient,GitHubRateLimitError,GitHubRequestError
        from tests.test_github_client import FakeResponse
        body={'errors':[{'type':'RATE_LIMIT','code':'graphql_rate_limit'},{'type':'FORBIDDEN'}]}
        c=GitHubClient(opener=lambda *a,**kw:FakeResponse(body),token_provider=lambda:'isolated-test')
        try:c.get_repositories_batch(('github/gitignore',))
        except GitHubRateLimitError:self.fail('independent permission error hidden')
        except GitHubRequestError:pass
        else:self.fail('invalid result accepted')

    def test_concurrent_quota_confirmation_is_single_flight(self):
        from concurrent.futures import ThreadPoolExecutor
        b=RequestBudget(0,30,float('inf'));entered=threading.Event();release=threading.Event();calls=[]
        def refresh(budget,resource):
            calls.append(resource);entered.set();self.assertTrue(release.wait(2))
            with budget._lock:budget.core_remaining=4
        b.quota_refresh=refresh
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(b.can_spend,'core',1,0);self.assertTrue(entered.wait(2))
            other=pool.submit(b.can_spend,'core',1,0);release.set()
            self.assertTrue(a.result(timeout=2));self.assertTrue(other.result(timeout=2))
        self.assertEqual(calls,['core'])

    def test_already_known_secondary_cooldown_finishes_quota_round(self):
        import time
        from github_radar.github_client import GitHubClient
        c=GitHubClient(opener=lambda *a,**kw:self.fail('known cooldown must not request again'))
        c.retry_not_before=time.time()+30;event=threading.Event()
        b=RequestBudget(0,30,float('inf'),quota_event=event);b.quota_refresh=c.reconcile_quota
        self.assertFalse(b.can_spend('core',1,0));self.assertTrue(event.is_set())
