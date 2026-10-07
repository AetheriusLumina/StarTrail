import tempfile,unittest,threading,time
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from github_radar.storage import RadarStore
from github_radar.search_coordinator import SearchCoordinator
from github_radar.search_sources import SearchSources
from github_radar.search_storage import SearchStore
from github_radar.search_types import SearchScope,AISearchResult,ObservedRepository,SearchProgress
from github_radar.discovery_types import DiscoveryCandidate,RequestBudget,SearchPage
from github_radar.models import OfficialStarWeek
from tests.test_search_coordinator import FakeAI
from tests.test_storage import repository

class ApprovedSearchTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.store=RadarStore(t.name)
  self.now=datetime(2026,10,5,12,tzinfo=timezone.utc);self.keyword=self.store.add_keyword('skills')
  self.scope=SearchScope('keyword',self.keyword.id,self.now.date().isoformat(),None,'skills',1000,'model')
 def engine(self,scope,count=60):
  ai=FakeAI();reads=[]
  def readme(name,**kw):reads.append(name);return 'skills README'
  client=SimpleNamespace(readme_excerpt=readme,core_remaining=5000,search_remaining=30)
  sources=SimpleNamespace(limited=False,notes=[],collected=count,trendshift=None)
  def collect(scope,*args):SearchStore(self.store).save_candidates(scope,tuple(ObservedRepository(repository(i,10000+i),self.now.isoformat()) for i in range(1,count+1)),())
  sources.collect=collect
  return SearchCoordinator(self.store,client,ai,sources=sources,now=lambda:self.now,legacy_review=True),ai,reads
 def test_growth_uses_official_numbers_without_any_ai_assessment(self):
  scope=SearchScope('growth',None,self.scope.local_date,'2026-10-04','',100,'model')
  engine,ai,_=self.engine(scope)
  engine.client.star_history_weeks=lambda name:[OfficialStarWeek(1790467200,(1,2,3,4,5,6,7))]
  # September 27 UTC Sunday; index seven is the next week, so use Oct 4.
  engine.client.star_history_weeks=lambda name:[OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(100,0,0,0,0,0,0))]
  ai.assess_growth_batch=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('growth must not call AI'))
  result=engine.run(scope)
  self.assertEqual(result.status,'done');self.assertEqual(result.newly_checked,0)
  self.assertEqual(len(self.store.daily_recommendations(scope.local_date)),5)
 def test_keyword_analysis_receives_merged_group_in_one_call(self):
  engine,ai,reads=self.engine(self.scope)
  calls=[];original=ai.filter_batch
  def bulk(items,*a,**kw):calls.append(len(items));return original(items,*a,**kw)
  ai.filter_batch=bulk
  result=engine.run(self.scope)
  self.assertEqual(result.status,'done');self.assertEqual(calls,[60]);self.assertEqual(len(reads),60)
  self.assertEqual([r.repo_id for r in self.store.daily_recommendations(self.scope.local_date)],[60,59,58,57,56])
 def test_cap_exhausted_does_not_fetch_unknown_readmes(self):
  engine,ai,reads=self.engine(self.scope)
  engine.run(self.scope,max_new=20,continue_search=True)
  self.assertEqual(ai.checked,20);self.assertEqual(len(reads),20)
 def test_bulk_search_reuses_its_official_metadata_before_individual_gets(self):
  calls=[]
  class Client:
   def search_page(self,*a,**kw):calls.append('page');return SearchPage((repository(7,9000),),1,False)
   def get_repository(self,name):calls.append('get');return repository(7,9000)
  candidate=DiscoveryCandidate(repository(7,9000).full_name,None,('ai_web_search',),self.now.isoformat())
  sources=SearchSources(Client(),self.store,None,None,lambda:0)
  scope=replace(self.scope,local_date=datetime.now().astimezone().date().isoformat())
  sources.collect(scope,None,AISearchResult((candidate,),(),(),1),RequestBudget(20,20,100),threading.Event(),lambda *a:None)
  self.assertEqual(calls,['page'])

class RoundRobinTests(unittest.TestCase):
 def test_modules_advance_in_turn_but_publish_in_original_priority(self):
  from github_radar.search_jobs import SearchJobs
  from github_radar.service import RadarService
  with tempfile.TemporaryDirectory() as t:
   store=RadarStore(t);store.add_keyword('skills');now=datetime.now().astimezone();calls=[]
   def factory():
    engine=SimpleNamespace(provider=SimpleNamespace(cancel=lambda:None))
    def steps(scope,**kw):
     name=scope.section;p=SearchProgress(name,name,scope.keyword_id,scope.local_date,status='running',stage='collecting')
     calls.append((name,'collect'));yield p
     calls.append((name,'check'));yield replace(p,stage='publishing')
     self.assertTrue(engine.can_publish());calls.append((name,'publish'));return replace(p,status='done',stage='complete')
    engine.run_steps=steps;return engine
   jobs=SearchJobs(RadarService(SimpleNamespace(),store),store,factory,now=lambda:now)
   result=jobs.refresh(now.date().isoformat(),now.isoformat())
   self.assertEqual(result.status,'ok')
   self.assertLess(calls.index(('keyword','collect')),calls.index(('growth','publish')))
   self.assertLess(calls.index(('keyword','check')),calls.index(('growth','publish')))
   self.assertLess(calls.index(('growth','publish')),calls.index(('keyword','publish')))

class CatalogCoordinatorTests(ApprovedSearchTests):
 def test_entire_250_catalog_and_same_day_cache(self):
  engine,ai,reads=self.engine(self.scope,250);calls=[]
  def catalog(items,keyword,terms,model,**kw):
   calls.append(len(items));return ai.filter_batch(items,keyword,model,**kw)
  ai.filter_catalog=catalog
  result=engine.run(self.scope);self.assertEqual(result.status,'done',result.notes)
  self.assertEqual(calls,[250]);self.assertEqual(len(reads),200)
  engine.run(self.scope);self.assertEqual(calls,[250])
 def test_today_old_growth_occupant_not_discarded_before_new_publication(self):
  from github_radar.models import Recommendation,StarSnapshot
  repo=repository(60,10060);day=self.scope.local_date;at=self.now.isoformat()
  self.store.commit_daily(day,[repo],[StarSnapshot(repo.id,day,repo.stars,at)],
   [Recommendation(repo.id,day,'growth',None,10,'2026-10-04',at,rank=1,metric_basis='github_daily_new')])
  engine,ai,reads=self.engine(self.scope,60);calls=[]
  def catalog(items,keyword,terms,model,**kw):
   calls.extend(i.repo.id for i in items);return ai.filter_batch(items,keyword,model,**kw)
  ai.filter_catalog=catalog;iterator=engine.run_steps(self.scope);engine.can_publish=lambda:False
  for progress in iterator:
   if progress.stage=='publishing':break
  self.assertIn(60,calls)
  with __import__('contextlib').closing(self.store._connect()) as db:db.execute("DELETE FROM recommendations WHERE section='growth'");db.commit()
  engine.can_publish=lambda:True
  for _ in iterator:pass
  self.assertEqual(self.store.daily_recommendations(day)[0].repo_id,60)

class CancelBudgetTests(ApprovedSearchTests):
 def test_close_during_preparation_restores_client_budget_and_releases_lease(self):
  engine,ai,_=self.engine(self.scope);original=RequestBudget(4999,30,time.monotonic()+90)
  engine.client.budget=original;engine.client.clock=time.monotonic
  iterator=engine.run_steps(self.scope)
  for progress in iterator:
   if progress.stage=='preparing':break
  iterator.close();self.assertIs(engine.client.budget,original);self.assertIs(engine.client.clock,time.monotonic)
  self.assertTrue(SearchStore(self.store).claim_run(self.scope,'successor',now=self.now.timestamp()).acquired)
