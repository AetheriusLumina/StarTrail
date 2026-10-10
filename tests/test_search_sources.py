import tempfile
import unittest
import threading
from dataclasses import replace
from github_radar.storage import RadarStore
from github_radar.search_sources import SearchSources
from github_radar.search_storage import SearchStore
from github_radar.search_types import SearchScope,QueryExpansion,AISearchResult
from github_radar.discovery_types import SearchPage,RequestBudget,DiscoveryCandidate
from tests.test_storage import repository

class SearchSourcesTests(unittest.TestCase):
    def test_growth_trend_metadata_is_ready_before_broad_official_search(self):
        from types import SimpleNamespace
        from datetime import datetime,timezone
        events=[];scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-03')
        repo=replace(repository(99,2000),full_name='org/trending')
        def get(name):events.append('metadata');return repo
        def page(*a,**kw):
            events.append('search')
            self.assertGreater(SearchStore(self.store).candidate_count(scope),0,'trend must be resolved before search consumes quota')
            return SearchPage((),0,False)
        source=SearchSources(SimpleNamespace(get_repository=get,search_page=page),self.store,SimpleNamespace(repo_names=lambda:('org/trending',)),None,lambda:0)
        source.now=lambda:datetime(2026,10,4,10,tzinfo=timezone.utc)
        it=source.collect_steps(scope,None,None,self.budget,threading.Event(),lambda *a:None)
        next(it)
        while 'metadata' not in events:next(it)
        self.assertNotIn('search',events)
        self.assertIn(99,source.official_page_ids,'resolved trends must be offered to immediate daily measurement')
        list(it)

    def test_current_trend_metadata_yields_for_scoring_before_any_search(self):
        from types import SimpleNamespace
        from datetime import datetime,timezone
        from github_radar.search_types import ObservedRepository
        scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-03')
        now=datetime(2026,10,4,10,tzinfo=timezone.utc);repo=replace(repository(99,2000),full_name='org/trending')
        SearchStore(self.store).save_candidates(scope,(ObservedRepository(repo,now.isoformat()),),())
        def forbidden(*a,**kw):raise AssertionError('search must wait for current trend scoring')
        source=SearchSources(SimpleNamespace(get_repository=forbidden,search_page=forbidden),self.store,SimpleNamespace(repo_names=lambda:('org/trending',)),None,lambda:0)
        source.now=lambda:now
        it=source.collect_steps(scope,None,None,self.budget,threading.Event(),lambda *a:None)
        next(it);next(it)
        self.assertEqual(source.official_page_ids,{99});it.close()

    def test_cached_trending_yields_before_unresolved_source_metadata(self):
        from types import SimpleNamespace
        from datetime import datetime,timezone
        from github_radar.search_types import ObservedRepository
        scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-03')
        now=datetime(2026,10,4,10,tzinfo=timezone.utc);repo=replace(repository(99,2000),full_name='org/trending')
        SearchStore(self.store).save_candidates(scope,(ObservedRepository(repo,now.isoformat()),),())
        def forbidden(*a,**kw):raise AssertionError('cached Trending must be scored before unresolved lower-priority metadata')
        source=SearchSources(SimpleNamespace(graphql_remaining=100,get_repositories_batch=forbidden),self.store,None,None,lambda:0)
        source.now=lambda:now
        candidates=(DiscoveryCandidate('org/trending',99,('github_trending',),now.isoformat()),DiscoveryCandidate('org/secondary',None,('trendshift_daily',),now.isoformat()))
        it=source._collect_authenticated_metadata_steps(candidates,scope,self.budget,threading.Event(),lambda *a:None)
        next(it);self.assertEqual(source.official_page_ids,{99});it.close()

    def test_trending_save_error_is_not_swallowed_as_auxiliary_network_failure(self):
        import sqlite3
        from types import SimpleNamespace
        from unittest.mock import patch
        source=SearchSources(SimpleNamespace(),self.store,SimpleNamespace(repo_names=lambda:('org/new',)),None,lambda:0)
        with patch.object(self.store,'save_discovery_batch',side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaises(sqlite3.OperationalError):next(source.collect_steps(self.scope,None,None,self.budget,threading.Event(),lambda *a:None))

    def test_successful_daily_source_is_kept_when_later_topic_fails(self):
        from types import SimpleNamespace
        from contextlib import closing
        from github_radar.discovery_types import DiscoveryBatch
        from github_radar.public_http import SourceRequestError
        batch=DiscoveryBatch('trendshift_daily',(DiscoveryCandidate('org/new',None,('trendshift_daily',),'2026-10-04T10:00:00Z'),),None,True,())
        def topic(*a):raise SourceRequestError('site topic unavailable')
        source=SearchSources(SimpleNamespace(),self.store,None,SimpleNamespace(daily=lambda *a:(batch,),topic=topic),lambda:0)
        iterator=source.collect_steps(self.scope,QueryExpansion(self.scope.term,(),('ai skills',),'1'),None,self.budget,threading.Event(),lambda *a:None)
        next(iterator);iterator.close()
        with closing(self.store._connect()) as db:self.assertIsNotNone(db.execute("SELECT full_name FROM discovery_catalog WHERE full_name='org/new'").fetchone())

    def test_trending_identity_and_evidence_are_saved_before_query_or_cancel(self):
        from types import SimpleNamespace
        from contextlib import closing
        source=SearchSources(SimpleNamespace(),self.store,SimpleNamespace(repo_names=lambda:('org/new',)),None,lambda:0)
        iterator=source.collect_steps(self.scope,None,None,self.budget,threading.Event(),lambda *a:None)
        next(iterator);iterator.close()
        with closing(self.store._connect()) as db:
            self.assertIsNotNone(db.execute("SELECT full_name FROM discovery_catalog WHERE full_name='org/new'").fetchone())
            evidence=db.execute("SELECT observed_at FROM source_evidence WHERE full_name='org/new' AND source_name='github_trending'").fetchone()
            self.assertIsNotNone(evidence)

    def test_productive_query_gets_extra_page_without_starving_other_terms(self):
        calls=[]
        class Client:
            def search_page(_self,q,page=1,**kw):
                calls.append((q,page))
                offset=1000 if '"novel"' in q else 2000 if '"slow"' in q else 0
                return SearchPage(tuple(repository(offset+page*100+i,2000) for i in range(100)),500,False)
        source=SearchSources(Client(),self.store,None,None,lambda:0)
        # Prime a prior productivity observation: novel yields new candidates,
        # while the other lane has been heavily duplicated.
        SearchStore(self.store).save_cursor(self.scope,'github-yield:novel',{'requests':2,'new_candidates':200})
        SearchStore(self.store).save_cursor(self.scope,'github-yield:slow',{'requests':20,'new_candidates':1})
        self.budget.search_remaining=4
        source.collect(self.scope,QueryExpansion(self.scope.term,('novel','slow'),(),'1'),None,self.budget,threading.Event(),lambda *a:None)
        self.assertEqual(len(calls),4)
        self.assertTrue(any('"slow"' in q for q,_ in calls[:3]))
        self.assertIn('"novel"',calls[3][0])

    def test_source_candidates_without_metadata_quota_are_durable_not_lost(self):
        from types import SimpleNamespace
        from github_radar.discovery_types import DiscoveryBatch
        batch=DiscoveryBatch('trendshift_daily',(DiscoveryCandidate('org/new',None,('trendshift_daily',),'2026-10-04T10:00:00Z'),),None,True,())
        client=SimpleNamespace(search_page=lambda *a,**kw:SearchPage((),0,False))
        source=SearchSources(client,self.store,None,SimpleNamespace(daily=lambda *a:(batch,)),lambda:0)
        self.budget.core_remaining=0
        source.collect(self.scope,None,None,self.budget,threading.Event(),lambda *a:None)
        from contextlib import closing
        with closing(self.store._connect()) as db:
            self.assertIsNotNone(db.execute("SELECT full_name FROM discovery_catalog WHERE full_name='org/new'").fetchone())
        self.assertTrue(source.quota_limited);self.assertEqual(source.metadata_pending,1)

    def test_keywords_share_pages_instead_of_original_query_using_all_quota(self):
        calls=[]
        class Client:
            def search_page(_self,q,**kw):
                calls.append(q);return SearchPage(tuple(repository(i,2000) for i in range(1,101)),300,False)
        source=SearchSources(Client(),self.store,None,None,lambda:0)
        self.budget.search_remaining=2
        source.collect(self.scope,QueryExpansion(self.scope.term,('agent-skill',),(),'1'),None,self.budget,threading.Event(),lambda *a:None)
        self.assertEqual(len(calls),2)
        self.assertIn('"agent-skill"',calls[1]);self.assertTrue(source.quota_limited)

    def test_new_authenticated_metadata_is_batched_without_per_repository_rest(self):
        from github_radar.github_client import GitHubClient
        calls=[]
        repos={'example/project-'+str(i):replace(repository(i,2000+i),full_name='example/project-'+str(i)) for i in range(1,46)}
        client=GitHubClient(opener=lambda *a,**kw:None,token_provider=lambda:'isolated-token')
        def batch(names):
            calls.append(tuple(names));return {name.casefold():repos[name.casefold()] for name in names}
        def forbidden(*args):raise AssertionError('unnecessary per-repository REST')
        client.get_repositories_batch=batch;client.get_repository=forbidden
        source=SearchSources(client,self.store,None,None,lambda:0)
        candidates=[DiscoveryCandidate(r.full_name,r.id,('tracked',),'2026-10-04T10:00:00Z') for r in repos.values()]
        candidates += candidates[:5]
        list(source._collect_metadata_steps(candidates,self.scope,self.budget,threading.Event(),lambda *a:None))
        self.assertEqual(len(calls),3)
        self.assertEqual(sum(map(len,calls)),45)
        self.assertEqual(SearchStore(self.store).candidate_count(self.scope),45)
        self.assertFalse(source.limited)

    def test_next_day_search_opens_fresh_range_without_losing_old_cursor(self):
        from datetime import date
        source=SearchSources(None,self.store,None,None,lambda:0)
        old=[{'start':'2007-01-01','end':'2026-10-04','low':1000,'high':None,'page':2}]
        source._save_cursor(self.scope,self.scope.term,old)
        next_scope=replace(self.scope,local_date='2026-10-05')
        queue=source._queue(next_scope,next_scope.term,date(2007,1,1),date(2026,10,5))
        self.assertEqual(queue[0]['end'],'2026-10-05');self.assertEqual(queue[0]['page'],1)
        self.assertEqual(queue[0]['start'],'2026-10-05')
        self.assertEqual(queue[1:],old)

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=RadarStore(temp.name);rule=self.store.add_keyword('agent skills')
        self.scope=SearchScope('keyword',rule.id,'2026-10-04',None,rule.term,1000,None)
        self.budget=RequestBudget(10000,1000,1000,0)

    def test_more_than_thousand_partitioned_not_pool_truncated(self):
        calls=[]
        class Client:
            def search_page(_self,q,page=1,per_page=100,sort='stars'):
                calls.append(q)
                if len(calls)==1:return SearchPage((repository(1,2000),),1201,False)
                index=100 if '2007-01-01..' in q else 700
                return SearchPage(tuple(repository(i,1000+i) for i in range(index+(page-1)*100,index+page*100)),600,False)
        sources=SearchSources(Client(),self.store,None,None,lambda:0)
        sources.collect(self.scope,QueryExpansion(self.scope.term,(),(),'1'),None,self.budget,threading.Event(),lambda *a:None)
        self.assertGreater(len(calls),1)
        count=0;last=None
        while batch:=SearchStore(self.store).candidate_page(self.scope,last,100):count+=len(batch);last=batch[-1].repo.id
        self.assertGreater(count,1000)

    def test_same_day_overflow_splits_star_range(self):
        calls=[]
        class Client:
            def search_page(_self,q,page=1,per_page=100,sort='stars'):
                calls.append(q)
                return SearchPage((repository(7,5000),),1100 if len(calls)==1 else 1,False)
        sources=SearchSources(Client(),self.store,None,None,lambda:0)
        from datetime import date
        sources._search_range(self.scope,'skills',date(2026,10,4),date(2026,10,4),self.budget,threading.Event(),lambda *a:None)
        self.assertTrue(any('stars:1000..3000' in q for q in calls))
        self.assertTrue(any('stars:>=3001' in q for q in calls))

    def test_ai_aliases_resolve_real_id_and_cannot_override_counts(self):
        class Client:
            def search_page(_self,*args,**kwargs):return SearchPage((),0,False)
            def get_repository(_self,name):return replace(repository(7,3000),full_name='real/repo')
        sources=SearchSources(Client(),self.store,None,None,lambda:0)
        result=AISearchResult(tuple(DiscoveryCandidate(n,None,('ai_web_search',),'2026-10-04T10:00:00Z') for n in ('old/repo','real/repo')),(),(),1)
        sources.collect(self.scope,QueryExpansion(self.scope.term,(),(),'1'),result,self.budget,threading.Event(),lambda *a:None)
        items=SearchStore(self.store).candidate_page(self.scope,None)
        self.assertEqual(len(items),1);self.assertEqual(items[0].repo.id,7)
        self.assertEqual(items[0].repo.stars,3000)

    def test_keyword_value_cannot_inject_query_operator(self):
        calls=[]
        class Client:
            def search_page(_self,q,**kwargs):calls.append(q);return SearchPage((),0,False)
        scope=replace(self.scope,term='x" stars:0 OR archived:true')
        sources=SearchSources(Client(),self.store,None,None,lambda:0)
        sources.collect(scope,QueryExpansion(scope.term,(),(),'1'),None,self.budget,threading.Event(),lambda *a:None)
        self.assertIn('"x\\" stars:0 OR archived:true"',calls[0])

    def test_duplicate_source_preserves_all_evidence_one_metadata_request(self):
        from github_radar.discovery_types import SourceEvidence
        calls=[]
        class Client:
            def search_page(_self,*a,**kw):return SearchPage((),0,False)
            def get_repository(_self,name):calls.append(name);return replace(repository(7,3000),full_name='real/repo')
        evidence=lambda source:SourceEvidence(source,'real/repo','https://github.com/real/repo','2026-10-04T10:00:00Z','search',None,'none',None,None,None)
        candidates=tuple(DiscoveryCandidate('real/repo',None,(s,),'2026-10-04T10:00:00Z',evidence=(evidence(s),)) for s in ('ai_web_search','trendshift_daily'))
        sources=SearchSources(Client(),self.store,None,None,lambda:0)
        sources.collect(self.scope,None,AISearchResult(candidates,(),(),1),self.budget,threading.Event(),lambda *a:None)
        self.assertEqual(calls,['real/repo'])
        self.assertEqual({e.source_name for e in self.store.source_evidence(7)},{'ai_web_search','trendshift_daily'})


    def test_foreground_reads_next_page_when_first_hundred_are_seen(self):
        calls=[]
        class Client:
            def search_page(_self,q,page=1,**kw):
                calls.append(page)
                return SearchPage(tuple(repository(i,4000+i) for i in range((page-1)*100+1,min(page*100,150)+1)),150,False)
        sources=SearchSources(Client(),self.store,None,None,lambda:0)
        sources.collect(self.scope,QueryExpansion(self.scope.term,(),(),'1'),None,self.budget,threading.Event(),lambda *a:None)
        self.assertEqual(calls,[1,2]);self.assertFalse(sources.limited)
        self.assertEqual(SearchStore(self.store).candidate_count(self.scope),150)

    def test_completed_scan_is_not_reopened_by_repeated_same_day_updates(self):
        from datetime import date
        calls=[]
        class Client:
            def search_page(_self,q,**kw):calls.append(q);return SearchPage((),0,False)
        sources=SearchSources(Client(),self.store,None,None,lambda:0)
        for _ in range(2):sources._search_range(self.scope,self.scope.term,date(2007,1,1),date(2026,10,4),self.budget,threading.Event(),lambda *a:None)
        self.assertEqual(len(calls),1)


class GrowthDiscoveryWindowTests(SearchSourcesTests):
 def test_daily_growth_discovers_recent_repositories_and_retains_old_catalog(self):
  from datetime import datetime,timezone
  from github_radar.search_types import ObservedRepository
  calls=[]
  class Client:
   def search_page(_self,q,**k):calls.append(q);return SearchPage((),0,False)
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-03')
  old=repository(7,5000)
  SearchStore(self.store).save_candidates(scope,(ObservedRepository(old,'2026-09-01T00:00:00+00:00'),),())
  source=SearchSources(Client(),self.store,None,None,lambda:0);source.daily_discovery=True
  source.collect(scope,None,None,self.budget,threading.Event(),lambda *a:None)
  self.assertEqual(len(calls),1);self.assertIn('created:2026-09-04..2026-10-04',calls[0])
  self.assertEqual(SearchStore(self.store).candidate_count(scope),1)
 def test_background_catalog_uses_separate_all_age_cursor(self):
  calls=[]
  class Client:
   def search_page(_self,q,**k):calls.append(q);return SearchPage((),0,False)
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-03')
  source=SearchSources(Client(),self.store,None,None,lambda:0);source.daily_discovery=True
  source.collect(scope,None,None,self.budget,threading.Event(),lambda *a:None)
  source.daily_discovery=False
  source.collect(scope,None,None,self.budget,threading.Event(),lambda *a:None)
  self.assertEqual(len(calls),2);self.assertIn('created:2007-01-01',calls[1])


class OptionalSourceStatusTests(SearchSourcesTests):
 def test_topic_first_page_is_visible_partial_coverage_not_a_failed_fetch(self):
  from types import SimpleNamespace
  from github_radar.discovery_types import DiscoveryBatch
  batch=DiscoveryBatch('trendshift_topic',(DiscoveryCandidate('real/skills',None,('trendshift_topic',),'2026-10-04'),),'cursor',False,('公开主题首批，未覆盖续页',))
  adapter=SimpleNamespace(daily=lambda *a:(),topic=lambda *a:batch)
  class Client:
   def search_page(_self,*a,**k):return SearchPage((),0,False)
   def get_repository(_self,name):return replace(repository(7,3000),full_name=name)
  source=SearchSources(Client(),self.store,None,adapter,lambda:0)
  source.collect(self.scope,QueryExpansion(self.scope.term,(),('ai skills',),'1'),None,self.budget,threading.Event(),lambda *a:None)
  self.assertFalse(source.limited,'valid finite supplementary source must not permanently block the official ranking')
  self.assertEqual(source.source_status['trendshift_topic'],{'status':'partial','count':1})
  self.assertIn('公开主题首批，未覆盖续页',source.notes)
 def test_source_failure_is_visible_and_other_sources_keep_working(self):
  from types import SimpleNamespace
  from github_radar.discovery_types import DiscoveryBatch
  adapter=SimpleNamespace(daily=lambda *a:(DiscoveryBatch('trendshift_daily',(),None,False,('connection failed',)),))
  class Client:
   def search_page(_self,*a,**k):return SearchPage((repository(7,3000),),1,False)
  source=SearchSources(Client(),self.store,None,adapter,lambda:0)
  source.collect(self.scope,None,None,self.budget,threading.Event(),lambda *a:None)
  self.assertFalse(source.limited);self.assertEqual(source.source_status['trendshift_daily']['status'],'failed')
  self.assertEqual(SearchStore(self.store).candidate_count(self.scope),1)
 def test_same_refresh_shares_public_pages_without_cross_refresh_staleness(self):
  from types import SimpleNamespace
  calls=[];shared={}
  adapter=SimpleNamespace(daily=lambda *a:(calls.append(1) or ()))
  class Client:
   def search_page(_self,*a,**k):return SearchPage((),0,False)
  for i in range(2):
   source=SearchSources(Client(),self.store,None,adapter,lambda:0);source.shared_pages=shared
   source.collect(self.scope,None,None,self.budget,threading.Event(),lambda *a:None)
  self.assertEqual(len(calls),1)
  source=SearchSources(Client(),self.store,None,adapter,lambda:0);source.shared_pages={}
  source.collect(self.scope,None,None,self.budget,threading.Event(),lambda *a:None)
  self.assertEqual(len(calls),2)


class KnownIdentityRecoveryTests(SearchSourcesTests):
 def test_metadata_collection_recovers_known_id_after_name_reassignment(self):
  from github_radar.github_client import GitHubRequestError
  for missing in (False,True):
   calls=[]
   class Client:
    def get_repository(_self,name):
     if missing:raise GitHubRequestError('missing',status=404)
     return replace(repository(999,3000),full_name=name)
    def get_repository_by_id(_self,identity):calls.append(identity);return replace(repository(7,3000),full_name='renamed/skills')
   source=SearchSources(Client(),self.store,None,None,lambda:0)
   candidate=DiscoveryCandidate('old/skills',7,('tracked',),'2026-10-04')
   for _ in source._collect_metadata_steps([candidate],self.scope,self.budget,threading.Event(),lambda *a:None):pass
   self.assertEqual(calls,[7]);self.assertEqual(SearchStore(self.store).candidate_count(self.scope),1)
   self.assertFalse(source.limited)


 def test_known_today_name_cannot_replace_tracked_identity(self):
  from github_radar.search_types import ObservedRepository
  calls=[];name='old/skills'
  class Client:
   def get_repository(_self,value):return replace(repository(999,3000),full_name=value)
   def get_repository_by_id(_self,identity):calls.append(identity);return replace(repository(7,3000),full_name='renamed/skills')
  SearchStore(self.store).save_candidates(self.scope,(ObservedRepository(replace(repository(999,3000),full_name=name),'2026-10-04T00:00:00Z'),),())
  source=SearchSources(Client(),self.store,None,None,lambda:0)
  for _ in source._collect_metadata_steps([DiscoveryCandidate(name,7,('tracked',),'2026-10-04')],self.scope,self.budget,threading.Event(),lambda *a:None):pass
  self.assertEqual(calls,[7]);self.assertEqual(SearchStore(self.store).candidate_count(self.scope),2)
  self.assertFalse(source.limited)

class ParallelOfficialBatchTests(SearchSourcesTests):
 def test_stale_metadata_uses_four_parallel_twenty_repository_batches(self):
  from github_radar.github_client import GitHubClient
  from github_radar.search_types import ObservedRepository
  barrier=threading.Barrier(4);widths=[]
  class Client(GitHubClient):
   def get_repositories_batch(_self,names):
    widths.append(len(names));barrier.wait(timeout=2)
    return {name.casefold():replace(repository(int(name.split('/')[-1]),3000),full_name=name) for name in names}
  client=Client(token_provider=lambda:'test-only');self.addCleanup(client.close)
  source=SearchSources(client,self.store,None,None,lambda:0)
  items=tuple(ObservedRepository(replace(repository(i,3000),full_name='repo/'+str(i)),'2026-10-01T00:00:00Z') for i in range(1,81))
  SearchStore(self.store).save_candidates(self.scope,items,())
  for _ in source.refresh_candidates_steps(self.scope,self.budget,threading.Event(),lambda *a:None):pass
  self.assertFalse(source.limited,source.notes);self.assertEqual(widths,[20]*4)
  self.assertEqual(SearchStore(self.store).candidate_count(self.scope),80)
