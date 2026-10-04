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
