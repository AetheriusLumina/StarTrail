"""Local evidence-backed discovery; AI only expands keyword meaning."""
import json,logging,threading
from dataclasses import asdict,replace
from datetime import datetime
from .ai_provider import AIOutputError
from .ai_types import AIRepositoryInput
from .discovery_types import RequestBudget,DiscoveryCandidate,SourceEvidence
from .github_client import GitHubClient,GitHubRequestError
from .models import StarSnapshot,GrowthCoverage
from .search_storage import _repository
from .search_timing import SearchTiming
from .search_types import AISearchResult,ObservedRepository,PreparedCandidate,Publication
from .search_coordinator import _digest,semantic_fingerprint
from .search_ranking import select_growth_candidates,select_keyword_candidates

def replay(data):
    candidates=tuple(DiscoveryCandidate(**{**c,'source_names':tuple(c['source_names']),'evidence':tuple(SourceEvidence(**{**e,'topics':tuple(e['topics'])}) for e in c['evidence'])}) for c in data['candidates'])
    return AISearchResult(candidates,tuple(data['coverage']),tuple(data['notes']),data['web_search_calls'])

def local_steps(engine,scope,*,continue_search=False,cancel_event=None,on_progress=None,defer_publish=False,prepare_only=False,budget_seconds=None):
    e=engine;event=cancel_event or threading.Event();started=e.clock();deadline=float("inf") if budget_seconds is None else started+max(1,budget_seconds)
    lease=e.search.claim_run(scope,e.owner,now=e.now().timestamp())
    if not lease.acquired:return e.search.progress(lease.job_id)
    e.current_lease=lease
    e.deferred_publication=None;e._timing=SearchTiming(e.clock);e._timing.resume();e._ai_seconds=0
    p=replace(e.search.progress(lease.job_id),status='running',stage='expanding',notes=(),started_at=e.now().isoformat(),judgment_calls=0,catalog_calls=0,newly_checked=0,checked_completed=0,matched_count=0,search_mode='discovery',limited=False,quota_limited=False,failed=False,pending=0,official_checked=0,cache_hits=0,metadata_pending=0)
    def metrics():return dict(elapsed_seconds=round(max(0,e.clock()-started),3),ai_seconds=round(e._ai_seconds,3),stage_seconds=e._timing.snapshot())
    last_saved=[started-1]
    def tick(**changes):
        nonlocal p
        if event.is_set():raise AIOutputError('检索已取消')
        if e.clock()>=deadline:raise AIOutputError('检索时间保护已到，候选及断点保留；榜单未保存')
        if changes.get('quota_limited') and getattr(e,'quota_event',None) is not None:e.quota_event.set()
        if changes.get('limited') and not changes.get('quota_limited') and 'failed' not in changes:changes['failed']=True
        if 'notes' in changes:changes['notes']=tuple(dict.fromkeys((*p.notes,*changes['notes'])))
        important=changes.get('stage',p.stage)!=p.stage or changes.get('limited',False)!=p.limited or 'ai_started_at' in changes
        e._timing.switch(changes.get('stage',p.stage));changes.update(metrics());p=replace(p,**changes)
        if not important and e.clock()-last_saved[0]<1:return
        last_saved[0]=e.clock()
        if not e.search.renew_run(lease,now=e.now().timestamp(),ttl=min(1900,max(1,deadline-e.clock()))):raise AIOutputError('任务已被其他更新替代')
        e.search.save_progress(p,lease=lease)
        if on_progress:on_progress(p)
    def ai_steps(call,*args,**kw):
        nonlocal p
        at=e.clock();tick(ai_started_at=e.now().isoformat());future=None
        def work():
            began=e.clock()
            try:return call(*args,**kw)
            finally:e._ai_seconds+=max(0,e.clock()-began)
        try:
            executor=getattr(e,'ai_executor',None)
            if executor is None:return work()
            future=executor.submit(work)
            while not future.done():
                tick();yield p
            return future.result()
        finally:
            if future and not future.done():
                e.provider.cancel();future.cancel()
                try:future.result(timeout=max(1,kw.get('timeout',120)+5))
                except Exception:pass
            p=replace(p,ai_started_at='')
    def timeout():return max(.01,min(120,deadline-e.clock()))
    def check_source_failure():
        if getattr(e.sources,'failed',False):
            tick(limited=True,failed=True,notes=tuple(e.sources.notes))
            raise AIOutputError('官方来源检索失败，整份旧榜保留')

    try:
        tick();yield p
        if isinstance(e.client,GitHubClient):e.client.renew_expired_quotas(now=e.now().timestamp())
        budget=RequestBudget(getattr(e.client,'core_remaining',None),getattr(e.client,'search_remaining',None),deadline,quota_event=getattr(e,'quota_event',None));e._active_budget=budget
        if isinstance(e.client,GitHubClient):budget.quota_refresh=e.client.reconcile_quota
        expansion=None
        if scope.section=='keyword':
            expansion=e.search.load_expansion(scope)
            if expansion is None and not prepare_only and not (budget.quota_event is not None and budget.quota_event.is_set()):
                tick(expansion_calls=p.expansion_calls+1)
                try:
                    # Read the site's actual directory; AI chooses only valid topics.
                    directory=()
                    adapter=getattr(e.sources,'trendshift',None)
                    if adapter is not None:
                        adapter.budget=budget;adapter.clock=e.clock
                        directory=adapter.topics()
                    expansion=yield from ai_steps(e.provider.expand_keyword,scope.term,scope.model_id,directory,timeout=timeout());e.search.save_expansion(scope,expansion)
                except AIOutputError as exc:tick(limited=True,notes=(*p.notes,'AI 扩词未完成，保留原词：'+str(exc)[:200]))
            if expansion:scope=replace(scope,expansion_hash=_digest(asdict(expansion)))
        e.search.import_candidates(scope)
        # Fixed public sources are fetched directly. AI only expands keywords;
        # neither board repeats a paid web search or audits repository facts.
        result=None
        e.sources.strict_keyword=True
        e.sources.daily_discovery=not prepare_only
        tick(stage='collecting')
        if hasattr(e.sources,'collect_steps'):
            for _ in e.sources.collect_steps(scope,expansion,result,budget,event,lambda stage,count:tick(stage=stage,collected=count)):
                check_source_failure()
                tick(candidate_pool=e.search.candidate_count(scope));yield p
                # Obtain official growth evidence while discovery still has
                # quota. Otherwise the first search limit can leave thousands
                # of metadata-only candidates and nothing eligible to publish.
                ids=getattr(e.sources,'official_page_ids',None)
                if scope.section=='growth' and ids:
                    page_ids=tuple(ids);ids.clear()
                    for _row,_day in e._frontier(scope,budget,tick,event,only_ids=page_ids):
                        if _row is None:yield p
        else:e.sources.collect(scope,expansion,result,budget,event,lambda stage,count:tick(stage=stage,collected=count))
        if hasattr(e.sources,'refresh_candidates_steps'):
            for _ in e.sources.refresh_candidates_steps(scope,budget,event,lambda stage,count:tick(stage=stage,collected=count)):
                check_source_failure();yield p
        check_source_failure()
        tick(candidate_pool=e.search.candidate_count(scope),source_status=dict(getattr(e.sources,'source_status',{})));yield p
        if prepare_only:
            if scope.section=='growth':
                for _ in e._frontier(scope,budget,tick,event):yield p
            p=replace(p,status='paused' if p.limited or e.sources.limited else 'prepared',stage='stopped' if p.limited or e.sources.limited else 'prepared',notes=tuple(dict.fromkeys((*p.notes,*e.sources.notes))),**metrics())
            e.search.save_progress(p,lease=lease);return p
        terms=tuple(dict.fromkeys((scope.term,*(expansion.terms if expansion else ()),*(expansion.topics if expansion else ()))))
        prepared=[];verdicts={};daily={};pending=0
        for row,day in e._frontier(scope,budget,tick,event):
            if row is None:yield p;continue
            tick(stage='matching' if scope.section=='keyword' else 'preparing')
            observation=ObservedRepository(_repository(json.loads(row['payload'])),row['observed_at']);repo=observation.repo
            if repo.archived or repo.stars<scope.min_stars:continue
            if not scope.local_date<=datetime.fromisoformat(observation.observed_at).astimezone(e.now().tzinfo).date().isoformat()<=e.now().date().isoformat():
                if budget.quota_event is not None and budget.quota_event.is_set():
                    pending+=1;continue
                observation=e.sources.resolve_candidate(DiscoveryCandidate(repo.full_name,repo.id,('tracked',),observation.observed_at),budget)
                if observation is None:pending+=1;continue
                repo=observation.repo;e.search.save_candidates(scope,(observation,),())
            if repo.archived or repo.stars<scope.min_stars:continue
            fingerprint=semantic_fingerprint(scope,repo,None,'local-discovery-v2')
            item=PreparedCandidate(observation,AIRepositoryInput(repo,None,True,observed_at=observation.observed_at),(),fingerprint,'',day)
            prepared.append(item)
            if scope.section=='keyword':
                verdicts[repo.id]=e.search.matching_verdict(scope,observation,terms,strict=True)
            else:daily[repo.id]=day
            if len(prepared)%20==0:yield p
        if budget.quota_event is not None and budget.quota_event.is_set():tick(limited=True,quota_limited=True)
        if scope.section=='keyword':e.search.save_judgments(scope,prepared,tuple(verdicts.values()),(),e.now().isoformat())
        if scope.section=='growth':pending=max(pending,p.pending)
        tick(stage='ranking',matched_count=sum(v.verdict=='relevant' for v in verdicts.values()),unique=len(prepared),pending=pending,limited=p.limited or e.sources.limited or bool(pending),quota_limited=p.quota_limited or getattr(e.sources,'quota_limited',False),failed=p.failed or getattr(e.sources,'failed',e.sources.limited),metadata_pending=getattr(e.sources,'metadata_pending',0),notes=tuple(dict.fromkeys((*p.notes,*e.sources.notes,*(result.notes if result else ())))))
        observations=tuple(item.observation for item in prepared)
        snapshots=tuple(StarSnapshot(o.repo.id,scope.local_date,o.repo.stars,o.observed_at) for o in observations)
        coverage=GrowthCoverage(p.candidate_pool,p.official_checked,('github_search','github_trending','trendshift'),scope.stat_date,'github_daily_new',stop_reasons=p.notes) if scope.section=='growth' else None
        def ranked(occupied,own=()):
            seen=e.store.seen_repo_ids_before(scope.local_date)
            return select_keyword_candidates(scope,prepared,verdicts,seen,occupied,own) if scope.section=='keyword' else select_growth_candidates(scope,prepared,daily,None,seen,occupied)
        e.rerank=ranked
        current=e.store.daily_recommendations(scope.local_date);own={r.repo_id for r in current if r.section==scope.section and r.keyword_id==scope.keyword_id}
        picks=ranked({r.repo_id for r in current}-own,own)
        value=Publication(scope,lease.generation,observations,snapshots,picks,coverage)
        tick(stage='prepared' if defer_publish else 'publishing');yield p
        if p.failed or (p.limited and not p.quota_limited):raise AIOutputError('本轮检索或官方证据未完成，整份旧榜保留')
        if p.quota_limited and not prepared and (scope.section!='growth' or not p.official_checked):raise AIOutputError('本轮额度不足且没有可核实结果，整份旧榜保留')
        if defer_publish:
            e.deferred_publication=(value,lease);p=replace(p,status='ready',stage='prepared',**metrics())
        else:
            while not getattr(e,'can_publish',lambda:True)():yield p
            current=e.store.daily_recommendations(scope.local_date);own={r.repo_id for r in current if r.section==scope.section and r.keyword_id==scope.keyword_id}
            e.search.publish(replace(value,recommendations=ranked({r.repo_id for r in current}-own,own)),lease,now=e.now(),cancel_event=event)
            p=replace(p,status='partial' if p.quota_limited else 'done',stage='complete',**metrics())
        e.search.save_progress(p,lease=lease)
    except GeneratorExit:
        reason=getattr(e,'abort_reason',None)
        p=replace(p,status='paused' if reason else 'canceled',stage='stopped',limited=True,notes=(*p.notes,reason or '检索已取消，断点保留'),**metrics());e.search.save_progress(p,lease=lease);raise
    except Exception as exc:
        p=replace(p,status='canceled' if event.is_set() else 'paused',stage='stopped',limited=True,failed=not event.is_set(),ai_started_at='',notes=tuple(dict.fromkeys((*p.notes,str(exc)[:300]))),**metrics());e.search.save_progress(p,lease=lease)
    finally:
        logging.getLogger(__name__).info('search timing %s',json.dumps({'section':scope.section,'keyword_id':scope.keyword_id,'status':p.status,'candidate_pool':p.candidate_pool,'official_checked':p.official_checked,**metrics()},ensure_ascii=False))
        if e.deferred_publication is None:
            e.current_lease=None;e.search.finish_run(lease)
    return p
