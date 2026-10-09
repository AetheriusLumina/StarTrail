import json, sqlite3, tempfile, threading, unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from github_radar.storage import RadarStore
from github_radar.search_storage import SearchStore
from github_radar.search_sources import SearchSources
from github_radar.search_types import SearchScope, ObservedRepository
from github_radar.discovery_types import RequestBudget
from github_radar.github_client import GitHubClient
from github_radar.software_update import ReleaseClient, SoftwareUpdateError
from tests.test_storage import repository

class LockAndScaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=RadarStore(self.tmp.name);self.search=SearchStore(self.store)
        self.scope=SearchScope('growth',None,'2026-10-09','2026-10-08','',100,None)

    def test_current_schema_reopen_does_not_need_write_lock(self):
        with closing(self.store._connect()) as writer:
            writer.execute('BEGIN IMMEDIATE')
            original=sqlite3.connect
            def quick(*a,**kw):
                kw['timeout']=.05;return original(*a,**kw)
            with patch('github_radar.storage.sqlite3.connect',side_effect=quick):
                RadarStore(self.tmp.name)

    def test_frontier_work_scales_with_rows_not_pages_times_rows(self):
        self.search.save_candidates(self.scope,tuple(ObservedRepository(repository(i+1,1000+i),'2026-10-08T00:00:00Z') for i in range(3000)),())
        ops=[0];connect=self.store._connect
        def counted():
            c=connect()
            def progress():
                ops[0]+=1000
                return int(ops[0]>900000)
            c.set_progress_handler(progress,1000);return c
        with patch.object(self.store,'_connect',side_effect=counted):
            rows=list(self.search.frontier(self.scope))
        self.assertEqual(len(rows),3000)
        self.assertEqual(rows[0]['repo_id'],3000)

    def test_exhausted_graphql_does_not_request_each_stale_batch(self):
        self.search.save_candidates(self.scope,tuple(ObservedRepository(repository(i+1,1000),'2026-10-08T00:00:00Z') for i in range(100)),())
        client=GitHubClient(token_provider=lambda:'test');client.graphql_remaining=0
        source=SearchSources(client,self.store,None,None,lambda:0)
        with patch.object(client,'get_repositories_batch',return_value={}) as batch:
            list(source.refresh_candidates_steps(self.scope,RequestBudget(10,0,float('inf')),threading.Event(),lambda *a:None))
        batch.assert_not_called();self.assertTrue(source.quota_limited)

    def test_duplicate_launch_handoffs_before_constructing_store(self):
        from github_radar.__main__ import _main
        with patch('github_radar.browser_launcher.handoff_existing',return_value=True,create=True),patch('github_radar.storage.RadarStore',side_effect=AssertionError('unnecessary migration')):
            self.assertEqual(_main(['--data-dir',self.tmp.name]),0)

    def test_network_check_failure_is_not_reported_as_invalid_format(self):
        def offline(*a,**kw):raise TimeoutError('timeout')
        with self.assertRaisesRegex(SoftwareUpdateError,'网络'):
            ReleaseClient(offline).check()

    def test_identical_candidate_import_does_not_rewrite_rows(self):
        item=ObservedRepository(repository(1,1000),'2026-10-09T00:00:00Z')
        self.search.save_candidates(self.scope,(item,),())
        changes=[];connect=self.store._connect
        class Tracked(sqlite3.Connection):
            def close(self):changes.append(self.total_changes);super().close()
        def tracked():
            c=sqlite3.connect(self.store.db_path,factory=Tracked);c.row_factory=sqlite3.Row;return c
        with patch.object(self.store,'_connect',side_effect=tracked):self.search.save_candidates(self.scope,(item,),())
        self.assertEqual(sum(changes),0)

    def test_refresh_progress_updates_inside_network_wave(self):
        from datetime import datetime
        from types import SimpleNamespace
        from github_radar.search_jobs import SearchJobs
        from github_radar.search_types import SearchProgress
        from github_radar.service import RadarService
        now=datetime.now().astimezone();service=RadarService(SimpleNamespace(),self.store)
        def steps(scope,**kw):
            p=SearchProgress('live',scope.section,scope.keyword_id,scope.local_date,status='running',stage='measuring',official_checked=1)
            self.assertIn('on_progress',kw)
            kw['on_progress'](p)
            self.assertEqual(jobs.foreground_progress,[p])
            yield p
            return __import__('dataclasses').replace(p,status='done')
        jobs=SearchJobs(service,self.store,lambda:SimpleNamespace(run_steps=steps),ready=lambda:True,now=lambda:now)
        jobs.refresh(now.date().isoformat(),now.isoformat())

    def test_critical_keyword_failure_does_not_wait_for_growth(self):
        from datetime import datetime
        from types import SimpleNamespace,MethodType
        from dataclasses import replace
        from github_radar.search_jobs import SearchJobs
        from github_radar.search_coordinator import SearchCoordinator
        from github_radar.service import RadarService
        now=datetime.now().astimezone();service=RadarService(SimpleNamespace(),self.store)
        self.store.add_keyword('skills');growth_steps=[]
        def steps(engine,scope,**kw):
            lease=engine.search.claim_run(scope,engine.owner,now=now.timestamp())
            progress=replace(engine.search.progress(lease.job_id),status='running',stage='publishing' if scope.section=='keyword' else 'collecting')
            engine.search.save_progress(progress,lease=lease)
            yield progress
            if scope.section=='keyword':
                progress=replace(progress,status='paused',failed=True,limited=True,notes=('network unavailable',))
                engine.search.save_progress(progress,lease=lease)
                return progress
            for i in range(10):
                growth_steps.append(i);yield progress
            return progress
        def factory():
            e=SearchCoordinator(self.store,service.client,SimpleNamespace(cancel=lambda:None),now=lambda:now)
            e._run_steps=MethodType(steps,e);return e
        jobs=SearchJobs(service,self.store,factory,ready=lambda:True,now=lambda:now)
        result=jobs.refresh(now.date().isoformat(),now.isoformat())
        self.assertEqual(result.status,'error')
        self.assertLessEqual(len(growth_steps),2)

    def test_first_quota_stop_is_shared_across_modules_and_sources(self):
        event=threading.Event()
        first=RequestBudget(10,0,float('inf'),quota_event=event)
        second=RequestBudget(5000,30,float('inf'),quota_event=event)
        source=SearchSources(object(),self.store,None,None,lambda:0)
        source.quota_event=event
        source._quota_stop()
        self.assertTrue(event.is_set())
        self.assertFalse(second.can_spend('core',1,0))
        self.assertFalse(second.can_spend('search',1,0))
        self.assertFalse(second.can_spend('external',1,0))

    def test_startup_lock_has_controlled_error_instead_of_traceback(self):
        from github_radar.__main__ import _main
        with patch('github_radar.browser_launcher.handoff_existing',return_value=False),patch('github_radar.storage.RadarStore',side_effect=sqlite3.OperationalError('database is locked')):
            self.assertEqual(_main(['--data-dir',self.tmp.name]),1)

    def test_growth_obtains_daily_evidence_before_search_quota_ends_round(self):
        from datetime import datetime,timezone
        from dataclasses import replace
        from types import SimpleNamespace
        from github_radar.search_coordinator import SearchCoordinator
        from github_radar.discovery_types import SearchPage
        from github_radar.models import OfficialStarWeek
        now=datetime(2026,10,9,12,tzinfo=timezone.utc);calls=[]
        class Client:
            core_remaining=5000;search_remaining=1
            def search_page(self,*a,**kw):
                return SearchPage(tuple(replace(repository(i,1000+i),full_name=f'org/r{i}') for i in range(1,101)),200,False)
            def star_history_weeks(self,name):
                calls.append(name)
                return (OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(1,2,3,4,5,6,7)),)
        client=Client();sources=SearchSources(client,self.store,None,None,lambda:0)
        e=SearchCoordinator(self.store,client,SimpleNamespace(),sources=sources,now=lambda:now,clock=lambda:0)
        e.quota_event=threading.Event();sources.quota_event=e.quota_event
        result=e.run(self.scope)
        self.assertTrue(result.quota_limited)
        self.assertEqual(result.status,'partial',result.notes)
        self.assertEqual(len(calls),100)
        self.assertEqual(len(self.store.daily_recommendations(self.scope.local_date)),5)

    def test_same_star_frontier_uses_linear_index_seeks(self):
        self.search.save_candidates(self.scope,tuple(ObservedRepository(repository(i+1,1000),'2026-10-08T00:00:00Z') for i in range(6000)),())
        ops=[0];connect=self.store._connect
        def counted():
            c=connect()
            def progress():
                ops[0]+=1000
                return int(ops[0]>500000)
            c.set_progress_handler(progress,1000);return c
        with patch.object(self.store,'_connect',side_effect=counted):
            rows=list(self.search.frontier(self.scope))
        self.assertEqual([r['repo_id'] for r in rows],list(range(1,6001)))

    def test_queued_request_after_quota_stop_is_not_a_network_failure(self):
        from datetime import datetime,timezone
        from types import SimpleNamespace
        from github_radar.search_coordinator import SearchCoordinator
        from github_radar.github_client import GitHubRateLimitError
        now=datetime(2026,10,9,12,tzinfo=timezone.utc)
        self.search.save_candidates(self.scope,tuple(ObservedRepository(repository(i,1000),now.isoformat()) for i in (1,2)),())
        stopped=threading.Event();second_entered=threading.Event();counter=[0];mutex=threading.Lock()
        class Budget(RequestBudget):
            def spend(self,*a):
                with mutex:counter[0]+=1;index=counter[0]
                if index==2:
                    second_entered.set()
                    if not stopped.wait(3):raise AssertionError('quota was not latched')
                return super().spend(*a)
        client=GitHubClient(request_concurrency=2)
        def rate_limited(*a):
            if not second_entered.wait(3):raise AssertionError('second worker did not start')
            raise GitHubRateLimitError('rate limited')
        client.star_history_weeks=rate_limited
        e=SearchCoordinator(self.store,client,SimpleNamespace(),now=lambda:now,clock=lambda:0)
        changes=[]
        list(e._frontier(self.scope,Budget(5000,30,float('inf'),quota_event=stopped),lambda **kw:changes.append(kw),threading.Event()))
        failures=[v for v in changes if v.get('limited') and not v.get('quota_limited')]
        self.assertEqual(failures,[])
        self.assertTrue(any(v.get('quota_limited') for v in changes))

    def test_first_quota_boundary_atomically_publishes_both_boards(self):
        from datetime import datetime,timezone
        from dataclasses import replace
        from types import SimpleNamespace
        from github_radar.search_coordinator import SearchCoordinator
        from github_radar.search_jobs import SearchJobs
        from github_radar.search_types import QueryExpansion
        from github_radar.discovery_types import SearchPage
        from github_radar.models import OfficialStarWeek
        from github_radar.service import RadarService
        now=datetime(2026,10,9,12,tzinfo=timezone.utc);calls=[]
        rule=self.store.add_keyword('skills',100)
        keyword=SearchScope('keyword',rule.id,self.scope.local_date,None,'skills',100,None)
        self.search.save_expansion(keyword,QueryExpansion('skills',(),(),'1'))
        self.store.save_search_enabled(True)
        class Client:
            core_remaining=5000;search_remaining=2
            def search_page(self,query,**kw):
                calls.append(('search',query))
                base=1000 if 'skills' in query else 0
                return SearchPage(tuple(replace(repository(base+i,1000+i),full_name=f'org/skills-{base+i}',topics=('skills',)) for i in range(1,101)),300,False)
            def star_history_weeks(self,name):
                calls.append(('daily',name))
                return (OfficialStarWeek(int(datetime(2026,10,4,tzinfo=timezone.utc).timestamp()),(1,2,3,4,5,6,7)),)
        client=Client();service=RadarService(client,self.store,clock=lambda:0)
        engines=[]
        def factory():
            sources=SearchSources(client,self.store,None,None,lambda:0)
            engine=SearchCoordinator(self.store,client,SimpleNamespace(cancel=lambda:None),sources=sources,now=lambda:now,clock=lambda:0)
            engines.append(engine);return engine
        jobs=SearchJobs(service,self.store,factory,ready=lambda:True,model=lambda:None,now=lambda:now)
        result=jobs.refresh(self.scope.local_date,now.isoformat())
        self.assertEqual(result.status,'ok',result.message)
        self.assertTrue(all(p.status=='partial' and p.quota_limited and not p.failed for p in jobs.progress),jobs.progress)
        saved=self.store.daily_recommendations(self.scope.local_date)
        self.assertEqual({r.section for r in saved},{'growth','keyword'})
        growth=[r for r in saved if r.section=='growth']
        self.assertEqual(len(growth),min(5,len([c for c in calls if c[0]=='daily'])))
        self.assertEqual(len([r for r in saved if r.section=='keyword']),5)
        self.assertEqual(len({r.repo_id for r in saved}),len(saved))
        timing=self.search.refresh_timing()
        self.assertTrue(timing['quota_scoped'])
        self.assertEqual(timing['published_at'],self.store.daily_updated_at(self.scope.local_date))
        self.assertFalse(self.store.has_incomplete_search(self.scope.local_date))

    def test_cross_rule_import_does_not_rescan_catalog_each_page(self):
        from dataclasses import replace
        old=replace(self.scope,model_id='old')
        self.search.save_candidates(old,tuple(ObservedRepository(repository(i+1,1000),'2026-10-08T00:00:00Z') for i in range(6000)),())
        ops=[0];connect=self.store._connect
        def counted():
            c=connect()
            def progress():
                ops[0]+=1000
                return int(ops[0]>1500000)
            c.set_progress_handler(progress,1000);return c
        with patch.object(self.store,'_connect',side_effect=counted):self.search.import_candidates(self.scope)
        self.assertEqual(self.search.candidate_count(self.scope),6000)

    def test_previous_schema_marker_migrates_new_import_index(self):
        with closing(self.store._connect()) as db,db:
            db.execute('DROP INDEX idx_search_candidate_import')
            db.execute('PRAGMA user_version=6')
            cookie=db.execute('PRAGMA schema_version').fetchone()[0]
            db.execute("UPDATE settings SET value=? WHERE name='schema_cookie'",(str(cookie),))
        reopened=RadarStore(self.tmp.name)
        with closing(reopened._connect()) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],8)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='idx_search_candidate_import'").fetchone())

    def test_growth_catalog_join_and_score_updates_are_indexed(self):
        self.search.save_candidates(self.scope,tuple(ObservedRepository(repository(i+1,1000),'2026-10-08T00:00:00Z') for i in range(3000)),())
        with closing(self.store._connect()) as db,db:
            db.executemany('INSERT INTO discovery_catalog(catalog_key,repo_id,full_name,source_names,discovered_at,last_discovered_at) VALUES(?,?,?,?,?,?)',[(str(i),i,'org/r'+str(i),'[]','2026-10-08','2026-10-08') for i in range(1,3001)])
        ops=[0];connect=self.store._connect
        def counted():
            c=connect()
            def progress():
                ops[0]+=1000
                return int(ops[0]>1000000)
            c.set_progress_handler(progress,1000);return c
        with patch.object(self.store,'_connect',side_effect=counted):rows=list(self.search.growth_frontier(self.scope))
        self.assertEqual(len(rows),3000)
        with closing(self.store._connect()) as db:
            plan=' '.join(str(tuple(r)) for r in db.execute('EXPLAIN QUERY PLAN UPDATE discovery_catalog SET last_scored_at=? WHERE repo_id=?',('now',1)))
        self.assertIn('SEARCH',plan)

    def test_persisted_failure_stops_other_work_before_generator_finishes(self):
        from datetime import datetime
        from types import SimpleNamespace,MethodType
        from dataclasses import replace
        from github_radar.search_jobs import SearchJobs
        from github_radar.search_coordinator import SearchCoordinator
        from github_radar.service import RadarService
        now=datetime.now().astimezone();service=RadarService(SimpleNamespace(),self.store);work=[]
        def steps(engine,scope,**kw):
            lease=engine.search.claim_run(scope,engine.owner,now=now.timestamp())
            progress=replace(engine.search.progress(lease.job_id),status='running',stage='measuring',failed=True,limited=True,notes=('network retry failed',))
            engine.search.save_progress(progress,lease=lease)
            yield progress
            for i in range(10):work.append(i);yield progress
            return progress
        def factory():
            e=SearchCoordinator(self.store,SimpleNamespace(),SimpleNamespace(cancel=lambda:None),now=lambda:now)
            e.run_steps=MethodType(steps,e);return e
        self.store.save_search_enabled(True)
        jobs=SearchJobs(service,self.store,factory,ready=lambda:True,now=lambda:now)
        result=jobs.refresh(now.date().isoformat(),now.isoformat())
        self.assertEqual(result.status,'error');self.assertEqual(work,[])

    def test_heartbeat_retries_transient_database_lock(self):
        from types import SimpleNamespace
        from github_radar.search_jobs import SearchJobs
        jobs=SearchJobs(SimpleNamespace(),self.store,lambda:None)
        self.store.save_search_enabled(True)
        class Stop:
            def wait(self,seconds):return False
            def set(self):pass
        class Thread:
            def __init__(self,target,**kw):self.target=target
            def start(self):self.target()
        with patch('github_radar.search_jobs.threading.Event',return_value=Stop()),patch('github_radar.search_jobs.threading.Thread',Thread),patch.object(jobs,'_renew_leases',side_effect=[sqlite3.OperationalError('database is locked'),False]) as renew,patch.object(jobs,'cancel') as cancel:
            jobs._start_heartbeat(None,None)
        self.assertEqual(renew.call_count,2);cancel.assert_called_once()

    def test_metadata_connection_drop_retries_once(self):
        from urllib.error import URLError
        from github_radar.github_client import GitHubRequestError
        client=GitHubClient(opener=lambda *a,**kw:None)
        failure=GitHubRequestError('network');failure.__cause__=URLError('connection reset')
        with patch.object(client,'_get_repositories_batch_once',side_effect=[failure,{'org/repo':'official'}],create=True) as once:
            self.assertEqual(client.get_repositories_batch(('org/repo',)),{'org/repo':'official'})
        self.assertEqual(once.call_count,2)

    def test_source_failure_propagates_before_more_discovery(self):
        from datetime import datetime,timezone
        from types import SimpleNamespace
        from github_radar.search_coordinator import SearchCoordinator
        now=datetime(2026,10,9,12,tzinfo=timezone.utc);work=[]
        class Source:
            limited=False;failed=False;quota_limited=False;collected=0;notes=[]
            def collect_steps(self,*args):
                self.failed=True;self.limited=True;self.notes=['official metadata network failure']
                yield None
                work.append('unnecessary further discovery')
        e=SearchCoordinator(self.store,SimpleNamespace(),SimpleNamespace(),sources=Source(),now=lambda:now,clock=lambda:0)
        result=e.run(self.scope)
        self.assertEqual(result.status,'paused');self.assertTrue(result.failed)
        self.assertEqual(work,[])
        self.assertIn('official metadata network failure',result.notes)
