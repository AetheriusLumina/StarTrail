"""Approved local discovery: no automatic audit, stable numeric rankings."""
import tempfile,threading,unittest
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from contextlib import closing
from github_radar.storage import RadarStore
from github_radar.search_coordinator import SearchCoordinator
from github_radar.search_storage import SearchStore
from github_radar.search_types import SearchScope,QueryExpansion,AISearchResult,ObservedRepository,Publication
from github_radar.models import OfficialStarWeek,StarDay,StarSnapshot,Recommendation
from tests.test_storage import repository

class LocalDiscoveryTests(unittest.TestCase):
 def test_transient_network_failure_gets_one_later_wave_without_losing_prior_evidence(self):
  from github_radar.github_client import GitHubClient,GitHubRequestError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(1);client=GitHubClient(opener=lambda *a,**kw:None,request_concurrency=1)
  engine.client=client;client.core_remaining=5000;calls=[]
  def history(name):
   calls.append(name)
   if len(calls)<=2:raise GitHubRequestError('temporary network timeout') from TimeoutError()
   return [OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(42,0,0,0,0,0,0))]
  client.star_history_weeks=history
  p=engine.run(scope)
  self.assertEqual(p.status,'done',p.notes);self.assertEqual(len(calls),3)
  self.assertEqual(p.official_checked,1);self.assertEqual(p.pending,0)

 def test_secondary_limit_waits_before_quota_probe_and_retries_retained_row(self):
  from github_radar.github_client import GitHubClient,GitHubRateLimitError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(1);client=GitHubClient(opener=lambda *a,**kw:None,request_concurrency=1)
  engine.client=client;client.core_remaining=5000;calls=[]
  def limits(path):
   self.assertGreaterEqual(self.now.timestamp(),client.retry_not_before)
   return {'resources':{'core':{'remaining':4999,'reset':int(self.now.timestamp())+3600}}}
  client._get_json=limits
  def history(name):
   calls.append(name)
   if len(calls)==1:
    client.retry_not_before=self.now.timestamp()+2
    raise GitHubRateLimitError(int(client.retry_not_before))
   return [OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(42,0,0,0,0,0,0))]
  client.star_history_weeks=history
  class Event:
   def is_set(_):return False
   def wait(_,seconds):self.time[0]+=seconds;self.now+=timedelta(seconds=seconds);return False
  p=engine.run(scope,cancel_event=Event())
  self.assertEqual(p.status,'done',p.notes);self.assertEqual(len(calls),2)
  self.assertEqual(p.official_checked,1);self.assertEqual(p.pending,0)

 def test_quota_wait_resumes_at_server_reset_and_can_be_canceled(self):
  from github_radar.github_client import GitHubClient
  from github_radar.discovery_types import RequestBudget
  engine=self.engine(1);client=GitHubClient(opener=lambda *a,**kw:None)
  engine.client=client;calls=[];stages=[];reset=int(self.now.timestamp())+2
  def limits(path):
   calls.append(path);return {'resources':{'core':{'remaining':10 if len(calls)==1 else 50,'reset':reset}}}
  client._get_json=limits
  class Event:
   def is_set(_):return False
   def wait(_,seconds):self.time[0]+=seconds;self.now+=timedelta(seconds=seconds);return False
  budget=RequestBudget(10,30,float('inf'))
  def tick(**kw):stages.append(kw.get('stage'))
  iterator=engine._wait_core_steps(budget,tick,Event())
  while True:
   try:next(iterator)
   except StopIteration as done:result=done.value;break
  self.assertTrue(result);self.assertEqual(budget.core_remaining,50)
  self.assertEqual(len(calls),2);self.assertIn('waiting_quota',stages)
  canceled=threading.Event();canceled.set()
  iterator=engine._wait_core_steps(RequestBudget(10,30,float('inf')),tick,canceled)
  with self.assertRaises(StopIteration) as stopped:next(iterator)
  self.assertFalse(stopped.exception.value)

 def test_foreground_update_has_no_whole_run_time_cutoff(self):
  engine=self.engine(3);original=engine.sources.collect
  def slow_collect(*args):
   self.time[0]+=4000;return original(*args)
  engine.sources.collect=slow_collect
  p=engine.run(self.scope)
  self.assertEqual(p.status,'done',p.notes)
  self.assertGreater(p.elapsed_seconds,4000)
 def test_explicit_background_budget_still_bounds_preparation(self):
  engine=self.engine(3);original=engine.sources.collect
  def slow_collect(*args):
   self.time[0]+=100;return original(*args)
  engine.sources.collect=slow_collect
  p=engine.run(self.scope,prepare_only=True,budget_seconds=60)
  self.assertEqual(p.status,'paused')

 def test_failed_growth_retains_all_errors_and_unmeasured_count(self):
  from github_radar.github_client import GitHubRequestError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(2);calls=[]
  def fail(name):
   calls.append(name);raise GitHubRequestError('failure '+str(len(calls)))
  engine.client.star_history_weeks=fail
  p=engine.run(scope)
  self.assertEqual(p.status,'paused');self.assertEqual(p.pending,2);self.assertEqual(p.official_checked,0)
  self.assertIn('failure 1',p.notes);self.assertIn('failure 2',p.notes)

 def test_growth_history_404_rechecks_identity_and_excludes_only_unavailable_repository(self):
  from github_radar.github_client import GitHubRequestError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(1);ids=[]
  def history(name):raise GitHubRequestError('history missing',status=404)
  def identity(value):ids.append(value);raise GitHubRequestError('identity unavailable',status=404)
  engine.client.star_history_weeks=history;engine.client.get_repository_by_id=identity
  p=engine.run(scope)
  self.assertEqual(p.status,'done',p.notes);self.assertEqual(ids,[1]);self.assertEqual(p.pending,0)
  self.assertTrue(engine.search.unavailable(1,scope.local_date));self.assertEqual(p.official_checked,0)
 def test_existing_identity_with_unavailable_statistics_still_blocks_publication(self):
  from github_radar.github_client import GitHubRequestError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(1);ids=[]
  def history(name):raise GitHubRequestError('statistics missing',status=404)
  def identity(value):ids.append(value);return repository(value,10001)
  engine.client.star_history_weeks=history;engine.client.get_repository_by_id=identity
  p=engine.run(scope)
  self.assertEqual(p.status,'paused');self.assertEqual(ids,[1]);self.assertEqual(p.pending,1)
  self.assertFalse(engine.search.unavailable(1,scope.local_date))
 def test_history_404_recovers_renamed_identity_and_uses_its_current_name(self):
  from github_radar.github_client import GitHubRequestError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(1);names=[]
  def history(name):
   names.append(name)
   if name!='new/name':raise GitHubRequestError('old name missing',status=404)
   return [OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(42,0,0,0,0,0,0))]
  engine.client.star_history_weeks=history
  engine.client.get_repository_by_id=lambda value:replace(repository(value,10001),full_name='new/name')
  p=engine.run(scope)
  self.assertEqual(p.status,'done',p.notes);self.assertEqual(len(names),2);self.assertEqual(names[-1],'new/name')
  self.assertEqual(p.official_checked,1);self.assertEqual(p.pending,0)
  self.assertEqual(engine.search.candidate_page(scope,None)[0].repo.full_name,'new/name')

 def test_strict_matching_rejects_description_readme_and_owner_only(self):
  search=SearchStore(self.store)
  repo=replace(repository(7,4000),full_name='skills-owner/unrelated',description='skills',topics=())
  search.save_match(self.scope,repo,'skills','github_query',self.now.isoformat())
  observation=ObservedRepository(repo,self.now.isoformat())
  self.assertEqual(search.matching_verdict(self.scope,observation,('skills',),strict=True).verdict,'uncertain')
 def test_strict_matching_accepts_repository_name_or_topic_and_new_query_evidence(self):
  search=SearchStore(self.store)
  for repo in (replace(repository(7,4000),full_name='org/agent-skills',topics=()),replace(repository(8,4000),topics=('agent-skills',))):
   observation=ObservedRepository(repo,self.now.isoformat())
   self.assertEqual(search.matching_verdict(self.scope,observation,('agent skills',),strict=True).verdict,'relevant')
  repo=replace(repository(9,4000),full_name='org/unrelated',description='',topics=())
  search.save_match(self.scope,repo,'skills','github_name_topic',self.now.isoformat())
  self.assertEqual(search.matching_verdict(self.scope,ObservedRepository(repo,self.now.isoformat()),('skills',),strict=True).verdict,'relevant')

 def test_growth_retries_transient_network_once_without_excluding_identity(self):
  from github_radar.github_client import GitHubRequestError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(1);calls=[]
  def history(name):
   calls.append(name)
   if len(calls)==1:raise GitHubRequestError('temporary network timeout') from TimeoutError('socket timed out')
   return [OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(42,0,0,0,0,0,0))]
  engine.client.star_history_weeks=history
  p=engine.run(scope)
  self.assertEqual(p.status,'done',p.notes);self.assertEqual(len(calls),2)
  self.assertFalse(engine.search.unavailable(1,scope.local_date))
 def test_growth_network_retry_is_bounded_and_keeps_failure_pending(self):
  from github_radar.github_client import GitHubRequestError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(1);calls=[]
  def history(name):
   calls.append(name);raise GitHubRequestError('temporary network timeout') from TimeoutError('socket timed out')
  engine.client.star_history_weeks=history
  p=engine.run(scope)
  self.assertEqual(p.status,'paused');self.assertEqual(len(calls),2);self.assertEqual(p.pending,1)

 def test_growth_does_not_retry_http_rate_limit_as_network_failure(self):
  from github_radar.github_client import GitHubRateLimitError
  from urllib.error import HTTPError
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(1);calls=[]
  def history(name):
   calls.append(name);raise GitHubRateLimitError(None) from HTTPError('https://api.github.com',429,'limited',{},None)
  engine.client.star_history_weeks=history;p=engine.run(scope)
  self.assertEqual(p.status,'paused');self.assertEqual(len(calls),1)
 def test_strict_multiword_match_cannot_combine_unrelated_fields(self):
  repo=replace(repository(7,4000),full_name='org/skills-for-poker',topics=('agent-based-simulation',))
  self.assertEqual(SearchStore(self.store).matching_verdict(self.scope,ObservedRepository(repo,self.now.isoformat()),('agent skills',),strict=True).verdict,'uncertain')

 def test_rate_limit_stops_further_frontier_waves(self):
  from github_radar.github_client import GitHubClient
  from urllib.error import HTTPError
  import io
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(5);calls=[]
  def opener(request,timeout):
   calls.append(request.full_url);raise HTTPError(request.full_url,429,'limited',{'Retry-After':'60','x-ratelimit-remaining':'4999'},io.BytesIO())
  engine.client=GitHubClient(opener=opener,request_concurrency=1);engine.client.core_remaining=5000
  p=engine.run(scope,budget_seconds=10)
  self.assertEqual(p.status,'paused');self.assertEqual(len(calls),1);self.assertEqual(p.pending,5)

 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.store=RadarStore(temp.name)
  self.now=datetime(2026,10,5,12,tzinfo=timezone.utc);rule=self.store.add_keyword('skills',1000)
  self.scope=SearchScope('keyword',rule.id,'2026-10-05',None,'skills',1000,'model');self.time=[0.0];self.calls=[]
 def engine(self,count=250):
  def expand(*a,**kw):self.calls.append('expand');self.time[0]+=3;return QueryExpansion('skills',('agent skills',),(),'1')
  def search(*a,**kw):self.calls.append('search');self.time[0]+=7;return AISearchResult((),('finite public search',),(),1)
  def forbidden(*a,**kw):raise AssertionError('automatic audit or bulk README download is forbidden')
  provider=SimpleNamespace(expand_keyword=expand,search_repositories=search,filter_catalog=forbidden,filter_batch=forbidden,assess_growth_batch=forbidden,cancel=lambda:None)
  client=SimpleNamespace(core_remaining=5000,search_remaining=30,readme_excerpt=forbidden)
  sources=SimpleNamespace(limited=False,notes=[],collected=count,trendshift=None)
  def collect(scope,*a):
   SearchStore(self.store).save_candidates(scope,tuple(ObservedRepository(replace(repository(i,10000+i),description='A reusable skills collection',topics=('skills',)),self.now.isoformat()) for i in range(1,count+1)),())
  sources.collect=collect
  return SearchCoordinator(self.store,client,provider,sources=sources,clock=lambda:self.time[0],now=lambda:self.now)
 def test_no_ai_audit_or_readme_scan_and_more_than_200_candidates_rank_by_stars(self):
  p=self.engine().run(self.scope)
  self.assertEqual(p.status,'done',p.notes);self.assertEqual(p.judgment_calls,0)
  self.assertEqual([r.repo_id for r in self.store.daily_recommendations(self.scope.local_date)],[250,249,248,247,246])
  self.assertEqual(p.ai_seconds,3);self.assertEqual(p.elapsed_seconds,3);self.assertEqual(self.calls,['expand'])
 def test_keyword_expansion_uses_directly_fetched_topic_directory(self):
  engine=self.engine(4);calls=[]
  engine.sources.trendshift=SimpleNamespace(topics=lambda:('ai skills','ai agent'))
  def expand(term,model,topics,**kw):
   calls.append(topics);return QueryExpansion(term,('agent skills',),('ai skills',),'1')
  engine.provider.expand_keyword=expand
  p=engine.run(self.scope)
  self.assertEqual(p.status,'done',p.notes);self.assertEqual(calls,[('ai skills','ai agent')])
  engine.run(self.scope);self.assertEqual(len(calls),1,'cached expansion must not call AI again')
 def test_repeated_update_keeps_paid_discovery_usage_but_no_audit_cap(self):
  engine=self.engine();first=engine.run(self.scope,max_new=1);second=engine.run(self.scope,max_new=1)
  self.assertEqual((first.status,second.status),('done','done'))
  self.assertEqual(self.calls,['expand']);self.assertEqual(second.ai_seconds,0)
 def test_changed_metadata_does_not_reuse_query_match_for_unrelated_project(self):
  search=SearchStore(self.store);repo=replace(repository(7,4000),description='unrelated game')
  search.save_match(self.scope,repo,'skills','github_query',self.now.isoformat())
  self.assertEqual(search.matching_verdict(self.scope,ObservedRepository(repo,self.now.isoformat()),('skills',)).verdict,'relevant')
  changed=replace(repo,description='unrelated project changed',full_name='new/game')
  self.assertEqual(search.matching_verdict(self.scope,ObservedRepository(changed,self.now.isoformat()),('skills',)).verdict,'uncertain')
 def test_query_match_reuses_star_changes_but_not_keyword_changes(self):
  search=SearchStore(self.store);repo=repository(7,4000)
  search.save_match(self.scope,repo,'skills','github_query',self.now.isoformat())
  observation=ObservedRepository(replace(repo,stars=9000),self.now.isoformat())
  self.assertEqual(search.matching_verdict(self.scope,observation,('skills',)).verdict,'relevant')
  self.assertEqual(search.matching_verdict(replace(self.scope,term='photos'),observation,('photos',)).verdict,'uncertain')
 def test_day_cache_includes_zero_expires_and_never_crosses_statistics_date(self):
  search=SearchStore(self.store);search.save_star_day(7,StarDay('2026-10-04',0),self.now)
  self.assertEqual(search.load_star_day(7,'2026-10-04',self.now).added,0)
  self.assertIsNone(search.load_star_day(7,'2026-10-03',self.now))
  self.assertIsNone(search.load_star_day(7,'2026-10-04',self.now+timedelta(hours=6)))
  self.assertIsNone(search.load_star_day(7,'2026-10-04',self.now-timedelta(seconds=1)))
 def test_growth_reuses_day_evidence_on_next_update_without_an_audit(self):
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(8);calls=[]
  def history(name):calls.append(name);return [OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(42,0,0,0,0,0,0))]
  engine.client.star_history_weeks=history
  first=engine.run(scope);second=engine.run(scope)
  self.assertEqual((first.status,second.status),('done','done'));self.assertEqual(len(calls),8)
  self.assertEqual(first.official_checked,8);self.assertEqual(second.cache_hits,8)
  self.assertEqual(self.calls,[], 'growth must never call AI')
 def test_wrong_official_day_boundary_is_not_cached_as_zero_or_published(self):
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  engine=self.engine(3)
  engine.client.star_history_weeks=lambda name:[OfficialStarWeek(int(datetime(2026,10,4,1,tzinfo=timezone.utc).timestamp()),(42,0,0,0,0,0,0))]
  p=engine.run(scope);self.assertEqual(p.status,'paused');self.assertEqual(self.store.daily_recommendations(scope.local_date),[])
  self.assertIsNone(SearchStore(self.store).load_star_day(1,scope.stat_date,self.now))
 def test_saved_candidate_library_survives_model_change_without_500_truncation(self):
  search=SearchStore(self.store)
  search.save_candidates(self.scope,tuple(ObservedRepository(repository(i,1000+i),self.now.isoformat()) for i in range(1,602)),())
  new=replace(self.scope,model_id='new');search.import_candidates(new)
  self.assertEqual(search.candidate_count(new),601)
 def test_old_candidates_are_not_relabelled_today(self):
  search=SearchStore(self.store);old=(self.now-timedelta(days=3)).isoformat()
  search.save_candidates(self.scope,(ObservedRepository(repository(7),old),),())
  new=replace(self.scope,model_id='new');search.import_candidates(new)
  self.assertEqual(search.candidate_page(new,None)[0].observed_at,old)

class AtomicIssueTests(LocalDiscoveryTests):
 def publication(self,scope,lease,identity):
  repo=repository(identity,2000+identity);at=self.now.isoformat()
  return Publication(scope,lease.generation,(ObservedRepository(repo,at),),(StarSnapshot(identity,scope.local_date,repo.stars,at),),(Recommendation(identity,scope.local_date,scope.section,scope.keyword_id,None,None,at),),None)
 def test_second_module_configuration_failure_rolls_back_first_and_old_issue(self):
  search=SearchStore(self.store);other=self.store.add_keyword('photos',1000);b=replace(self.scope,keyword_id=other.id,term='photos')
  a_lease=search.claim_run(self.scope,'a',now=self.now.timestamp());b_lease=search.claim_run(b,'b',now=self.now.timestamp())
  old=self.publication(self.scope,a_lease,8);search.publish(old,a_lease,now=self.now)
  before=self.store.daily_recommendations(self.scope.local_date);seen=self.store.seen_repo_ids()
  self.store.set_keyword_min_stars(other.id,9000)
  with self.assertRaises(ValueError):search.publish_all((self.publication(self.scope,a_lease,1),self.publication(b,b_lease,2)),(a_lease,b_lease),now=self.now)
  self.assertEqual(self.store.daily_recommendations(self.scope.local_date),before);self.assertEqual(self.store.seen_repo_ids(),seen)
 def test_disk_failure_in_second_module_rolls_back_metadata_and_snapshots(self):
  search=SearchStore(self.store);other=self.store.add_keyword('photos',1000);b=replace(self.scope,keyword_id=other.id,term='photos')
  leases=(search.claim_run(self.scope,'a',now=self.now.timestamp()),search.claim_run(b,'b',now=self.now.timestamp()))
  with closing(self.store._connect()) as db,db:db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON snapshots WHEN NEW.repo_id=2 BEGIN SELECT RAISE(ABORT,'disk'); END")
  with self.assertRaises(Exception):search.publish_all(tuple(self.publication(s,l,i) for s,l,i in zip((self.scope,b),leases,(1,2))),leases,now=self.now)
  self.assertEqual(self.store.daily_recommendations(self.scope.local_date),[]);self.assertEqual(self.store.repositories_for_ids([1,2]),{})


class LocalJobsTests(unittest.TestCase):
 setUp=LocalDiscoveryTests.setUp
 engine=LocalDiscoveryTests.engine
 def jobs(self,fail_keyword=False):
  from github_radar.service import RadarService
  from github_radar.search_jobs import SearchJobs
  client=SimpleNamespace()
  service=RadarService(client,self.store,clock=lambda:self.time[0])
  def factory():
   e=self.engine(12)
   e.client.star_history_weeks=lambda name:[OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(42,0,0,0,0,0,0))]
   collect=e.sources.collect
   def maybe_fail(scope,*args):
    if fail_keyword and scope.section=='keyword':raise ValueError('keyword source failed')
    collect(scope,*args)
   e.sources.collect=maybe_fail
   return e
  return SearchJobs(service,self.store,factory,now=lambda:self.now,model=lambda:'model')
 def test_all_boards_publish_together_with_priority_and_real_wall_ai_timing(self):
  result=self.jobs().refresh(self.scope.local_date,self.now.isoformat())
  self.assertEqual(result.status,'ok',result.notes)
  rows=self.store.daily_recommendations(self.scope.local_date)
  self.assertEqual(len({r.repo_id for r in rows}),len(rows))
  self.assertEqual(len([r for r in rows if r.section=='growth']),5)
  self.assertEqual(len([r for r in rows if r.section=='keyword']),5)
  self.assertEqual((result.elapsed_seconds,result.ai_seconds),(3,3))
 def test_second_board_failure_rolls_back_issue_and_persists_first_board_pause(self):
  self.assertEqual(self.jobs().refresh(self.scope.local_date,self.now.isoformat()).status,'ok')
  before=self.store.daily_recommendations(self.scope.local_date)
  result=self.jobs(True).refresh(self.scope.local_date,self.now.isoformat())
  self.assertEqual(result.status,'error',result.notes)
  self.assertEqual(self.store.daily_recommendations(self.scope.local_date),before)
  growth=next(p for p in SearchStore(self.store).current_progress(self.scope.local_date) if p.section=='growth')
  self.assertEqual(growth.status,'paused')
 def test_candidate_import_pages_latest_observation_before_identity_limit(self):
  search=SearchStore(self.store)
  for i in range(102):
   search.save_candidates(replace(self.scope,model_id=str(i)),(ObservedRepository(repository(7,1000+i),(self.now+timedelta(seconds=i)).isoformat()),),())
  search.import_candidates(self.scope)
  self.assertEqual(search.candidate_page(self.scope,None)[0].repo.stars,1101)


class ReviewedEvidenceTests(LocalJobsTests):
 def test_older_shared_module_metadata_cannot_overwrite_latest_snapshot(self):
  search=SearchStore(self.store);other=self.store.add_keyword('photos',1000);b=replace(self.scope,keyword_id=other.id,term='photos')
  leases=(search.claim_run(self.scope,'a',now=self.now.timestamp()),search.claim_run(b,'b',now=self.now.timestamp()))
  values=[]
  for s,l,stars,at in ((self.scope,leases[0],9000,self.now.isoformat()),(b,leases[1],4000,(self.now-timedelta(minutes=1)).isoformat())):
   repo=repository(7,stars)
   values.append(Publication(s,l.generation,(ObservedRepository(repo,at),),(StarSnapshot(7,s.local_date,stars,at),),(),None))
  search.publish_all(tuple(values),leases,now=self.now)
  self.assertEqual(self.store.repositories_for_ids([7])[7].stars,9000)
 def test_ai_citation_for_another_repository_does_not_grant_match(self):
  from github_radar.search_sources import SearchSources
  from github_radar.discovery_types import DiscoveryCandidate,SourceEvidence,RequestBudget,SearchPage
  repo=replace(repository(7,4000),full_name='other/unrelated',description='A chess game')
  client=SimpleNamespace(get_repository=lambda n:repo,search_page=lambda *a,**k:SearchPage((),0,False))
  source=SearchSources(client,self.store,None,None,lambda:0)
  evidence=SourceEvidence(source_name='ai_web_search',full_name=repo.full_name,source_url='https://github.com/example/skills',observed_at=self.now.isoformat(),period='discovery',stat_date=None,rank_kind='search',source_rank=None,total_stars_text=None,daily_added_text=None,evidence_text='skills')
  candidate=DiscoveryCandidate(repo.full_name,None,('ai_web_search',),self.now.isoformat(),evidence=(evidence,))
  source.collect(self.scope,None,AISearchResult((candidate,),(),(),1),RequestBudget(5000,30,1000),threading.Event(),lambda *a:None)
  observation=SearchStore(self.store).candidate_page(self.scope,None)[0]
  self.assertEqual(SearchStore(self.store).matching_verdict(self.scope,observation,('skills',)).verdict,'uncertain')
 def test_id_recovery_when_old_name_now_belongs_to_another_repository(self):
  from github_radar.search_sources import SearchSources
  from github_radar.discovery_types import DiscoveryCandidate,RequestBudget
  old=replace(repository(7,4000),full_name='old/name');new=replace(old,full_name='new/name');calls=[]
  def by_id(i):calls.append(i);return new
  client=SimpleNamespace(get_repository=lambda name:replace(old,id=999),get_repository_by_id=by_id)
  sources=SearchSources(client,self.store,None,None,lambda:0)
  resolved=sources.resolve_candidate(DiscoveryCandidate(old.full_name,7,('tracked',),self.now.isoformat()),RequestBudget(5000,30,1000))
  self.assertIsNotNone(resolved);self.assertEqual(resolved.repo.full_name,'new/name');self.assertEqual(calls,[7])
 def test_known_404_is_ineligible_today_but_retryable_tomorrow(self):
  from github_radar.search_sources import SearchSources
  from github_radar.discovery_types import DiscoveryCandidate,RequestBudget
  from github_radar.github_client import GitHubRequestError
  def gone(*a):raise GitHubRequestError('not found',status=404)
  sources=SearchSources(SimpleNamespace(get_repository=gone,get_repository_by_id=gone),self.store,None,None,lambda:0)
  search=SearchStore(self.store);repo=repository(7,4000);search.save_candidates(self.scope,(ObservedRepository(repo,(self.now-timedelta(days=1)).isoformat()),),())
  sources.resolve_candidate(DiscoveryCandidate(repo.full_name,7,('tracked',),self.now.isoformat()),RequestBudget(5000,30,1000))
  self.assertFalse(sources.limited);self.assertTrue(search.unavailable(7,datetime.now().astimezone().date().isoformat()))
  self.assertFalse(search.unavailable(7,(datetime.now().astimezone()+timedelta(days=1)).date().isoformat()))


class LocalParallelTests(LocalJobsTests):
 def test_keyword_expansions_overlap_and_growth_never_uses_ai(self):
  from github_radar.service import RadarService
  from github_radar.search_jobs import SearchJobs
  self.store.add_keyword('agent skills',1000)
  barrier=threading.Barrier(2);entered=[]
  service=RadarService(SimpleNamespace(),self.store)
  def factory():
   e=self.engine(12);e.clock=service.clock
   e.client.star_history_weeks=lambda name:[OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(42,0,0,0,0,0,0))]
   def expand(term,*a,**k):
    entered.append(term);barrier.wait(timeout=2);return QueryExpansion(term,(),(),'1')
   def forbidden(*a,**k):raise AssertionError('fixed sources must be read directly without AI web search')
   e.provider.expand_keyword=expand;e.provider.search_repositories=forbidden;return e
  result=SearchJobs(service,self.store,factory,now=lambda:self.now,model=lambda:'model').refresh(self.scope.local_date,self.now.isoformat())
  self.assertEqual(result.status,'ok',result.notes);self.assertEqual(sorted(entered),['agent skills','skills'])
 def test_growth_catalog_is_paged_beyond_500_and_never_marked_seen_by_collection(self):
  from github_radar.search_sources import SearchSources
  from github_radar.discovery_types import DiscoveryBatch,DiscoveryCandidate,RequestBudget,SearchPage
  self.store.save_discovery_batch(DiscoveryBatch('verified',tuple(DiscoveryCandidate(repository(i).full_name,i,('keyword',),self.now.isoformat(),cached_repo=repository(i,4000)) for i in range(1,602)),None,True,()))
  scope=replace(self.scope,section='growth',keyword_id=None,term='',min_stars=100,stat_date='2026-10-04')
  sources=SearchSources(SimpleNamespace(search_page=lambda *a,**k:SearchPage((),0,False)),self.store,None,None,lambda:0)
  sources.collect(scope,None,None,RequestBudget(5000,30,1000),threading.Event(),lambda *a:None)
  self.assertEqual(SearchStore(self.store).candidate_count(scope),601);self.assertEqual(self.store.seen_repo_ids(),set())


class LocalPreparationTests(LocalJobsTests):
 def test_progress_writes_are_bounded_for_large_local_candidate_scan(self):
  engine=self.engine(250);calls=[];original=engine.search.save_progress
  def save(*a,**k):calls.append(1);return original(*a,**k)
  engine.search.save_progress=save
  p=engine.run(self.scope);self.assertEqual(p.status,'done',p.notes)
  self.assertLess(len(calls),30)
 def test_preparation_collects_facts_without_ai_calls_or_recommendation_history(self):
  engine=self.engine(12)
  p=engine.run(self.scope,prepare_only=True)
  self.assertEqual(p.status,'prepared',p.notes);self.assertEqual(self.calls,[])
  self.assertEqual(SearchStore(self.store).candidate_count(self.scope),12)
  self.assertEqual(self.store.seen_repo_ids(),set());self.assertEqual(self.store.daily_recommendations(self.scope.local_date),[])


class LocalUnwindTests(LocalJobsTests):
 def test_preparation_cancel_during_claim_is_not_reset(self):
  from unittest.mock import patch
  from github_radar.search_jobs import SearchJobs
  from github_radar.service import RadarService
  jobs=SearchJobs(RadarService(SimpleNamespace(),self.store),self.store,self.engine,now=lambda:self.now)
  original=SearchStore.claim_run
  def claim(*args,**kw):jobs.cancel();return original(*args,**kw)
  with patch.object(SearchStore,'claim_run',claim):jobs.prepare()
  with closing(self.store._connect()) as db:self.assertEqual(db.execute('SELECT count(*) FROM search_candidates').fetchone()[0],0)
 def test_async_ai_duration_is_measured_inside_worker_not_late_result_read(self):
  from concurrent.futures import Future
  engine=self.engine(1)
  class Executor:
   def submit(_self,fn,*a,**k):
    f=Future();f.set_result(fn(*a,**k));self.time[0]+=50;return f
  engine.ai_executor=Executor()
  result=engine.run(self.scope)
  self.assertEqual(result.status,'done',result.notes);self.assertEqual(result.ai_seconds,3)
