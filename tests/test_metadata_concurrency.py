"""Exercise real REST parsing and SQLite writes with a controlled transport."""
import threading,time,tempfile,unittest
from pathlib import Path
from github_radar.github_client import GitHubClient
from github_radar.discovery_types import RequestBudget,DiscoveryCandidate
from github_radar.search_sources import SearchSources
from github_radar.search_types import SearchScope,AISearchResult
from github_radar.search_storage import SearchStore
from github_radar.storage import RadarStore
from tests.test_github_client import FakeResponse,REPO_PAYLOAD

class MetadataTests(unittest.TestCase):
    def run_collection(self,count=6,remaining=100,delay=.035,workers=None,cancel_after=None):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        store=RadarStore(folder.name);rule=store.add_keyword('skills')
        scope=SearchScope('keyword',rule.id,'2026-10-05',None,rule.term,1000,None)
        lock=threading.Lock();active=peak=0;calls=[];progress=[];cancel=threading.Event()
        def opener(request,timeout):
            nonlocal active,peak
            if '/search/' in request.full_url:return FakeResponse({'items':[],'total_count':0,'incomplete_results':False})
            name=request.full_url.rsplit('/repos/',1)[-1];idx=int(name.split('r')[-1])
            with lock:active+=1;peak=max(peak,active);calls.append(name)
            time.sleep(delay*(2 if idx%2 else 1))
            with lock:active-=1
            return FakeResponse(dict(REPO_PAYLOAD,id=idx,full_name=name,stargazers_count=2000+idx),{'X-RateLimit-Resource':'core','X-RateLimit-Remaining':str(1000-idx)})
        client=GitHubClient(opener=opener)
        source=SearchSources(client,store,None,None,time.monotonic)
        if workers is not None:source.metadata_workers=workers
        candidates=tuple(DiscoveryCandidate('org/r'+str(i),None,('ai_web_search',),'2026-10-05') for i in range(1,count+1))
        result=AISearchResult(candidates+(candidates[0],),(),(),1)
        budget=RequestBudget(remaining,1,time.monotonic()+30,10)
        def updated(stage,n):
            progress.append(n)
            if cancel_after and n>=cancel_after:cancel.set()
        start=time.perf_counter();source.collect(scope,None,result,budget,cancel,updated)
        rows=SearchStore(store).candidate_page(scope,None,100)
        return peak,calls,[(r.repo.id,r.repo.stars) for r in rows],progress,time.perf_counter()-start,budget
    def test_four_inflight_bounded_and_same_order_results_as_serial(self):
        concurrent=self.run_collection();serial=self.run_collection(workers=1)
        self.assertEqual(concurrent[0],4);self.assertEqual(serial[0],1)
        self.assertEqual(concurrent[2:4],serial[2:4]);self.assertEqual(len(concurrent[1]),6)
        self.assertEqual(len(set(concurrent[1])),6)
    def test_unknown_quota_starts_with_one_probe(self):
        result=self.run_collection(count=1,remaining=None)
        self.assertEqual(result[0],1);self.assertEqual(result[2],[(1,2001)])
    def test_reserve_is_not_spent_or_reopened_by_stale_headers(self):
        result=self.run_collection(remaining=12)
        self.assertEqual(len(result[1]),2);self.assertEqual(result[-1].core_remaining,10)
    def test_cancel_does_not_enqueue_remaining_candidates(self):
        result=self.run_collection(count=12,cancel_after=1)
        self.assertEqual(len(result[1]),4)
    def test_out_of_order_headers_do_not_increase_available_quota(self):
        budget=RequestBudget(20,2,100,10)
        budget.spend('core',1,0);budget.observe({'X-RateLimit-Resource':'core','X-RateLimit-Remaining':'15'})
        budget.observe({'X-RateLimit-Resource':'core','X-RateLimit-Remaining':'18'})
        self.assertEqual(budget.core_remaining,15)

class AuthRaceTests(unittest.TestCase):
    def test_delayed_old_identity_401_does_not_reopen_new_identity_reserve(self):
        from urllib.error import HTTPError
        barrier=threading.Barrier(2);learned=threading.Event();calls=[];errors=[];token=['expired'];rejections=[]
        budget=RequestBudget(100,1,time.monotonic()+10,10)
        def opener(request,timeout):
            calls.append((request.full_url,request.get_header('Authorization')))
            if request.get_header('Authorization')=='Bearer expired':
                barrier.wait(3)
                if request.full_url.endswith('r2'):learned.wait(3)
                raise HTTPError(request.full_url,401,'unauthorized',{},None)
            return FakeResponse(REPO_PAYLOAD,{'X-RateLimit-Resource':'core','X-RateLimit-Remaining':'10'})
        def reject(old):rejections.append(old);token[0]='renewed'
        client=GitHubClient(opener=opener,budget=budget,token_provider=lambda:token[0],on_auth_failure=reject)
        def request(name):
            try:client.get_repository(name)
            except Exception as exc:errors.append(exc)
            finally:
                if name.endswith('r1'):learned.set()
        threads=[threading.Thread(target=request,args=('org/r'+str(i),)) for i in (1,2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(rejections,['expired'])
        self.assertEqual(len(calls),3);self.assertEqual(budget.core_remaining,10);self.assertEqual(len(errors),1)

if __name__=='__main__':unittest.main()

class SameRefreshCacheTests(unittest.TestCase):
 def test_busy_optional_cache_does_not_discard_fetched_official_fact(self):
  from github_radar.search_storage import SearchRequestCache
  from tests.test_github_client import FakeResponse,REPO_PAYLOAD
  with tempfile.TemporaryDirectory() as folder:
   store=RadarStore(folder);cache=SearchRequestCache(store)
   client=GitHubClient(opener=lambda *a,**kw:FakeResponse(REPO_PAYLOAD));client.request_cache=cache
   blocker=store._connect();blocker.execute('BEGIN IMMEDIATE')
   at=time.monotonic()
   try:repo=client.get_repository('org/r1')
   finally:blocker.rollback();blocker.close()
   self.assertEqual(repo.id,REPO_PAYLOAD['id']);self.assertLess(time.monotonic()-at,1)
   self.assertIsNone(cache.get('/repos/org/r1'))
   client.get_repository('org/r1');self.assertIsNotNone(cache.get('/repos/org/r1'))
 def test_valid_public_get_reused_on_disk_without_spending_more_core(self):
  from github_radar.search_storage import SearchRequestCache
  from tests.test_github_client import FakeResponse,REPO_PAYLOAD
  with tempfile.TemporaryDirectory() as folder:
   store=RadarStore(folder);calls=[];budget=RequestBudget(100,10,time.monotonic()+10)
   client=GitHubClient(opener=lambda request,timeout:(calls.append(request.full_url) or FakeResponse(REPO_PAYLOAD)),budget=budget)
   client.request_cache=SearchRequestCache(store)
   self.assertEqual(client.get_repository('org/r1'),client.get_repository('org/r1'))
   self.assertEqual(len(calls),1);self.assertEqual(budget.core_remaining,99)
   client.request_cache=SearchRequestCache(store);client.get_repository('org/r1')
   self.assertEqual(len(calls),2,'a new refresh must not pretend the old response is fresh')

class SearchMetadataCacheTests(unittest.TestCase):
 def test_official_search_seeds_validated_metadata_for_other_modules(self):
  from github_radar.search_storage import SearchRequestCache
  from tests.test_github_client import FakeResponse,REPO_PAYLOAD
  with tempfile.TemporaryDirectory() as folder:
   calls=[];client=GitHubClient(opener=lambda request,timeout:(calls.append(request.full_url) or FakeResponse({'items':[dict(REPO_PAYLOAD,full_name='org/r1')],'total_count':1})))
   client.request_cache=SearchRequestCache(RadarStore(folder));repo=client.search_page('skills').items[0]
   self.assertEqual(client.get_repository('org/r1'),repo);self.assertEqual(len(calls),1)
