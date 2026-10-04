import tempfile
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from github_radar.storage import RadarStore
from github_radar.search_coordinator import SearchCoordinator
from github_radar.search_storage import SearchStore
from github_radar.search_types import SearchScope,QueryExpansion,AISearchResult,ObservedRepository
from github_radar.ai_types import RelevanceVerdict
from tests.test_storage import repository

class FakeAI:
    def __init__(self):self.expanded=0;self.searched=0;self.checked=0
    def expand_keyword(self,term,model_id,topics,**kw):
        self.expanded+=1;return QueryExpansion(term,('agent skills',),(),'1')
    def search_repositories(self,*args,**kw):
        self.searched+=1;return AISearchResult((),('public search',),(),1)
    def filter_batch(self,items,keyword,model_id,**kw):
        self.checked+=len(items);return tuple(RelevanceVerdict(i.repo.id,'relevant','README matches') for i in items)
    def cancel(self):pass

class SearchCoordinatorTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=RadarStore(temp.name);r=self.store.add_keyword('skills',1000)
        self.now=datetime.now().astimezone();self.scope=SearchScope('keyword',r.id,self.now.date().isoformat(),None,'skills',1000,'model')
        self.ai=FakeAI();self.client=SimpleNamespace(readme_excerpt=lambda *a,**kw:'skills README',core_remaining=5000,search_remaining=30)
        self.source=SimpleNamespace(limited=False,notes=[],collected=0,trendshift=None)
        self.source.collect=self.collect
        self.engine=SearchCoordinator(self.store,self.client,self.ai,sources=self.source,now=lambda:self.now)
    def collect(self,scope,expansion,result,budget,event,progress):
        SearchStore(self.store).save_candidates(scope,tuple(ObservedRepository(repository(i,10000+i),self.now.isoformat()) for i in range(1,26)),())
        self.source.collected=25
    def test_history_does_not_consume_new_daily_ai_budget(self):
        from datetime import timedelta
        from github_radar.models import Recommendation,StarSnapshot
        previous=(self.now.date()-timedelta(days=1)).isoformat()
        repos=[repository(i,10000+i) for i in range(1,21)]
        self.store.commit_daily(previous,repos,[StarSnapshot(r.id,previous,r.stars,previous+'T12:00:00+08:00') for r in repos],
            [Recommendation(r.id,previous,'keyword',self.scope.keyword_id,None,None,previous+'T12:00:00+08:00',rank=i+1) for i,r in enumerate(repos)])
        progress=self.engine.run(self.scope)
        self.assertEqual(progress.status,'done');self.assertEqual(self.ai.checked,5)

    def test_gap_search_happens_once_and_merges_then_reranks(self):
        def collect(scope,expansion,result,budget,event,progress):
            ids=range(1,4) if self.ai.searched==1 else range(1,8)
            SearchStore(self.store).save_candidates(scope,tuple(ObservedRepository(repository(i,10000+i),self.now.isoformat()) for i in ids),())
            self.source.collected=len(list(ids))
        self.source.collect=collect
        p=self.engine.run(self.scope)
        self.assertEqual(p.status,'done');self.assertEqual(self.ai.searched,2)
        self.assertEqual(self.ai.checked,7)
        self.assertEqual([r.repo_id for r in self.store.daily_recommendations(self.scope.local_date)],[7,6,5,4,3])
        self.engine.run(self.scope);self.assertEqual(self.ai.searched,2)

    def test_automatic_search_merges_and_ranks_before_publish(self):
        progress=self.engine.run(self.scope)
        self.assertEqual(progress.status,'done');self.assertEqual(self.ai.searched,1)
        self.assertEqual(self.ai.checked,25)
        self.assertEqual([r.repo_id for r in self.store.daily_recommendations(self.scope.local_date)],[25,24,23,22,21])
    def test_same_day_reuses_expansion_search_and_judgments(self):
        self.engine.run(self.scope);p=self.engine.run(self.scope)
        self.assertEqual((self.ai.expanded,self.ai.searched,self.ai.checked),(1,1,25))
        self.assertEqual(p.cache_hits,25);self.assertEqual(p.newly_checked,25)
    def test_cap_is_daily_not_reset_by_repeated_update(self):
        self.engine.run(self.scope,max_new=20);p=self.engine.run(self.scope,max_new=20)
        self.assertEqual(self.ai.checked,20);self.assertEqual(p.status,'partial')
        self.assertGreater(p.pending,0)
        self.engine.run(self.scope,max_new=20,continue_search=True)
        self.assertEqual(self.ai.checked,25)
    def test_canceled_work_does_not_publish(self):
        event=threading.Event();event.set()
        p=self.engine.run(self.scope,cancel_event=event)
        self.assertEqual(p.status,'canceled');self.assertEqual(self.ai.checked,0)
        self.assertEqual(self.store.daily_recommendations(self.scope.local_date),[])
    def test_source_failure_keeps_previous_result(self):
        self.engine.run(self.scope)
        before=self.store.daily_recommendations(self.scope.local_date)
        def fail(*args,**kw):raise OSError('offline')
        self.source.collect=fail
        p=self.engine.run(self.scope)
        self.assertEqual(p.status,'paused');self.assertEqual(before,self.store.daily_recommendations(self.scope.local_date))

    def test_cancel_at_publication_does_not_commit(self):
        event=threading.Event()
        p=self.engine.run(self.scope,cancel_event=event,on_progress=lambda p:event.set() if p.stage=='publishing' else None)
        self.assertEqual(p.status,'canceled')
        self.assertEqual(self.store.daily_recommendations(self.scope.local_date),[])

    def test_initial_ai_search_failure_still_collects_public_sources(self):
        def fail(*a,**kw):raise __import__('github_radar.ai_provider',fromlist=['AIOutputError']).AIOutputError('search failed')
        self.ai.search_repositories=fail
        p=self.engine.run(self.scope)
        self.assertEqual(self.source.collected,25)
        self.assertEqual(p.status,'partial')
        self.assertEqual(len(self.store.daily_recommendations(self.scope.local_date)),5)

    def test_optional_directory_failure_does_not_abort(self):
        from github_radar.public_http import SourceRequestError
        def fail(**kw):raise SourceRequestError('directory unavailable')
        self.source.trendshift=SimpleNamespace(topics=fail)
        p=self.engine.run(self.scope)
        self.assertEqual(self.source.collected,25)
        self.assertEqual(p.status,'partial')

    def test_successful_empty_coverage_is_done(self):
        self.source.collect=lambda *a,**kw:None
        p=self.engine.run(self.scope)
        self.assertEqual(p.status,'done')
        self.assertEqual(self.store.daily_recommendations(self.scope.local_date),[])

    def test_gap_search_checkpoint_replayed_after_collection_failure(self):
        from github_radar.discovery_types import DiscoveryCandidate
        def search(*a,**kw):
            self.ai.searched+=1
            ids=(1,2,3) if self.ai.searched==1 else (7,)
            return AISearchResult(tuple(DiscoveryCandidate(repository(i).full_name,i,('ai_web_search',),self.now.isoformat()) for i in ids),(),(),1)
        self.ai.search_repositories=search
        failed=[False]
        def collect(scope,expansion,result,budget,event,progress):
            if self.ai.searched==2 and not failed[0]:failed[0]=True;raise OSError('temporary outage')
            SearchStore(self.store).save_candidates(scope,tuple(ObservedRepository(repository(c.repo_id,10000+c.repo_id),self.now.isoformat()) for c in result.candidates),())
        self.source.collect=collect
        self.assertEqual(self.engine.run(self.scope).status,'paused')
        p=self.engine.run(self.scope)
        self.assertEqual(self.ai.searched,2)
        self.assertIn(7,[r.repo_id for r in self.store.daily_recommendations(self.scope.local_date)])

    def test_expansion_failure_preserves_original_public_search(self):
        from github_radar.ai_provider import AIOutputError
        def fail(*a,**kw):raise AIOutputError('quota')
        self.ai.expand_keyword=fail
        p=self.engine.run(self.scope)
        self.assertEqual(self.source.collected,25);self.assertEqual(p.status,'partial')
