"""Preparation and orchestration of a bounded, evidence-backed search task."""
import hashlib
import json
from dataclasses import asdict
from .ai_types import AIRepositoryInput
from .search_types import PreparedCandidate,ObservedRepository
from .github_client import GitHubRequestError, GitHubRateLimitError, GitHubClient

def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def semantic_fingerprint(scope,repo,readme_hash,extraction_version):
    # Popularity and observation dates change daily; meaning does not.
    return _digest([scope.section,scope.term,scope.model_id,scope.rules_version,scope.expansion_hash,
        repo.id,repo.full_name,repo.description,sorted(repo.topics),repo.language,
        readme_hash,extraction_version])

def evidence_fingerprint(scope,evidence,official_day):
    return _digest([scope.stat_date,asdict(official_day) if official_day else None,
        sorted(([e.source_name,e.full_name,e.source_url,e.period,e.stat_date,
                e.daily_added_text,e.day_boundary,e.repo_id,e.evidence_text] for e in evidence),key=lambda item:json.dumps(item,ensure_ascii=False))])

def prepare_candidate(observation,scope,readme_service,evidence,official_day):
    excerpt=None
    try:
        excerpt=readme_service.client.readme_excerpt(observation.repo.full_name,max_chars=6000)
    except (GitHubRequestError,OSError,ValueError):
        pass
    limited=not excerpt or len(excerpt)>=6000
    excerpt=excerpt[:6000] if excerpt else None
    readme_hash=_digest(excerpt)
    source=AIRepositoryInput(observation.repo,excerpt,limited,observed_at=observation.observed_at)
    return PreparedCandidate(observation,source,tuple(evidence),
        semantic_fingerprint(scope,observation.repo,readme_hash,'bounded-excerpt-v1'),
        evidence_fingerprint(scope,evidence,official_day),official_day)


import threading
from concurrent.futures import ThreadPoolExecutor
import time
import uuid
from contextlib import closing
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from .ai_provider import AIOutputError
from .discovery_types import RequestBudget,DiscoveryCandidate,SourceEvidence
from .models import StarSnapshot,GrowthCoverage
from .ranking import confirmed_star_days
from .search_storage import SearchStore,scope_key
from .search_types import QueryExpansion,AISearchResult,SearchProgress,Publication
from .search_ranking import select_keyword_candidates,select_growth_candidates
from .search_sources import SearchSources
from .public_http import SourceRequestError

class SearchCoordinator:
    """One module task: network outside transactions, lease checked at publication."""
    def __init__(self,store,client,provider,*,sources=None,now=None,clock=time.monotonic,legacy_review=False):
        self.store,self.client,self.provider=store,client,provider
        self.now=now or (lambda:datetime.now().astimezone());self.clock=clock
        self.search=SearchStore(store)
        self.sources=sources or SearchSources(client,store,None,None,clock)
        self.owner=uuid.uuid4().hex
        self.legacy_review=legacy_review

    def _readme(self,observation,scope,budget):
        # A small disk excerpt avoids repeated downloads and keeps the candidate
        # corpus off the in-memory model. Daily content refresh invalidates by hash.
        identity=observation.repo.id
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT * FROM search_readmes WHERE repo_id=?',(identity,)).fetchone()
        excerpt=None;limited=True
        if row and self.now()-datetime.fromisoformat(row['fetched_at'])<timedelta(hours=24):
            excerpt=row['excerpt'];limited=bool(row['limited'])
        elif budget.can_spend('core',1,self.clock()):
            if getattr(self.client,'budget',None) is not budget:budget.spend('core',1,self.clock())
            item=prepare_candidate(observation,scope,SimpleNamespace(client=self.client),(),None)
            excerpt=item.source.readme_excerpt;limited=item.source.source_limited
            if excerpt:
                with closing(self.store._connect()) as db,db:
                    db.execute('INSERT OR REPLACE INTO search_readmes VALUES(?,?,?,?)',
                        (identity,self.now().isoformat(),excerpt,int(limited)))
        return excerpt,limited

    def run(self,scope,**kwargs):
        iterator=self.run_steps(scope,**kwargs)
        while True:
            try:next(iterator)
            except StopIteration as done:return done.value

    def run_steps(self,scope,**kwargs):
        iterator=self._run_steps(scope,**kwargs);self._active_budget=None
        try:
            return (yield from self._drive_steps(iterator))
        finally:
            # Closing a suspended inner generator executes its own finally
            # blocks; restore shared client state around that unwind as well.
            budget=getattr(self.client,'budget',None);clock=getattr(self.client,'clock',None)
            try:iterator.close()
            finally:
                if hasattr(self.client,'budget'):self.client.budget=budget;self.client.clock=clock

    def _drive_steps(self,iterator):
        while True:
            previous_budget=getattr(self.client,'budget',None);previous_clock=getattr(self.client,'clock',None)
            if hasattr(self.client,'budget'):
                self.client.budget=self._active_budget;self.client.clock=self.clock
                if self._active_budget is not None:
                    # Another module may have consumed quota since this yield.
                    for resource in ('core','search'):
                        remaining=getattr(self.client,resource+'_remaining',None)
                        known=getattr(self._active_budget,resource+'_remaining')
                        if remaining is not None:setattr(self._active_budget,resource+'_remaining',remaining if known is None else min(remaining,known))
            if getattr(self,'_timing',None):self._timing.resume()
            try:value=next(iterator)
            except StopIteration as done:return done.value
            finally:
                if getattr(self,'_timing',None):self._timing.pause()
                if hasattr(self.client,'budget'):self.client.budget=previous_budget;self.client.clock=previous_clock
            yield value

    def _run_steps(self,scope,*,max_new=200,continue_search=False,cancel_event=None,on_progress=None,defer_publish=False,prepare_only=False,budget_seconds=1800):
        if not self.legacy_review:
            from .search_local import local_steps
            return (yield from local_steps(self,scope,continue_search=continue_search,cancel_event=cancel_event,on_progress=on_progress,defer_publish=defer_publish,prepare_only=prepare_only,budget_seconds=budget_seconds))
        if type(max_new) is not int or not 1<=max_new<=200:raise ValueError('核实上限必须在1到200之间')
        catalog_mode=scope.section=='keyword' and hasattr(self.provider,'filter_catalog')
        event=cancel_event or threading.Event();started=self.clock();deadline=started+1800
        lease=self.search.claim_run(scope,self.owner,now=self.now().timestamp())
        if not lease.acquired:return self.search.progress(lease.job_id)
        from .search_timing import SearchTiming
        self._timing=SearchTiming(self.clock);self._timing.resume();self._ai_seconds=0
        progress=replace(self.search.progress(lease.job_id),status='running',stage='expanding',notes=(),started_at=self.now().isoformat())
        def metrics():
            return dict(elapsed_seconds=round(max(0,self.clock()-started),3),
                        ai_seconds=round(self._ai_seconds,3),stage_seconds=self._timing.snapshot())
        def ai_call(call,*args,**kwargs):
            nonlocal progress
            at=self.clock()
            progress=replace(progress,ai_started_at=self.now().isoformat())
            self.search.save_progress(progress,lease=lease)
            if on_progress:on_progress(progress)
            try:return call(*args,**kwargs)
            finally:
                self._ai_seconds+=max(0,self.clock()-at)
                progress=replace(progress,ai_started_at='')
        cap=progress.newly_checked+max_new if continue_search else max_new
        last_tick=started
        def tick(**changes):
            nonlocal progress,last_tick
            if event.is_set():raise AIOutputError('检索已取消')
            if self.clock()>=deadline:raise AIOutputError('检索时间预算已到，结果及断点保留')
            if all(getattr(progress,key)==value for key,value in changes.items()) and self.clock()-last_tick<1:return
            last_tick=self.clock()
            self._timing.switch(changes.get('stage',progress.stage))
            changes.update(metrics())
            if not self.search.renew_run(lease,now=self.now().timestamp(),ttl=max(1,deadline-self.clock())):
                raise AIOutputError('任务已被其他更新替代')
            progress=replace(progress,**changes);self.search.save_progress(progress,lease=lease)
            if on_progress:on_progress(progress)
        def timeout():return max(.01,min(120,deadline-self.clock()))
        try:
            tick();yield progress
            # A long-lived page may retain yesterday's exhausted quota.
            # Refresh only expired server resets before constructing the budget.
            if isinstance(self.client, GitHubClient):
                self.client.renew_expired_quotas(now=self.now().timestamp())
            budget=RequestBudget(getattr(self.client,'core_remaining',None),getattr(self.client,'search_remaining',None),min(deadline,started+600));self._active_budget=budget
            expansion=None
            if scope.section=='keyword':
                expansion=self.search.load_expansion(scope)
                if not expansion:
                    topics=()
                    adapter=getattr(self.sources,'trendshift',None)
                    if adapter and hasattr(adapter,'topics'):
                        try:topics=adapter.topics(refresh=True)
                        except (SourceRequestError,OSError,ValueError) as exc:
                            tick(limited=True,notes=('Trendshift 主题目录: '+str(exc)[:200],))
                    tick(expansion_calls=progress.expansion_calls+1)
                    try:
                        expansion=ai_call(self.provider.expand_keyword,scope.term,scope.model_id,topics,timeout=timeout())
                        self.search.save_expansion(scope,expansion)
                    except AIOutputError as exc:
                        if event.is_set():raise
                        tick(limited=True,notes=(*progress.notes,'AI 扩词未完成，使用原词: '+str(exc)[:200]))
                if expansion is not None:scope=replace(scope,expansion_hash=_digest(asdict(expansion)))
            saved=self.search.cursor(scope,'ai-search')
            result=None
            if saved and saved['date']==scope.local_date:
                candidates=tuple(DiscoveryCandidate(**{**c,'source_names':tuple(c['source_names']),
                    'evidence':tuple(SourceEvidence(**{**e,'topics':tuple(e['topics'])}) for e in c['evidence'])}) for c in saved['candidates'])
                result=AISearchResult(candidates,tuple(saved['coverage']),tuple(saved['notes']),saved['web_search_calls'])
                extra=self.search.cursor(scope,'ai-gap')
                if extra and extra['date']==scope.local_date:
                    gap_candidates=tuple(DiscoveryCandidate(**{**c,'source_names':tuple(c['source_names']),
                        'evidence':tuple(SourceEvidence(**{**e,'topics':tuple(e['topics'])}) for e in c['evidence'])}) for c in extra['candidates'])
                    result=AISearchResult((*result.candidates,*gap_candidates),(*result.coverage,*extra['coverage']),
                        (*result.notes,*extra['notes']),result.web_search_calls+extra['web_search_calls'])
            elif progress.search_calls<2:
                tick(stage='searching',search_calls=progress.search_calls+1)
                try:
                    result=ai_call(self.provider.search_repositories,scope,expansion,(),None,timeout=timeout(),cancel_event=event)
                    self.search.save_cursor(scope,'ai-search',{'date':scope.local_date,**asdict(result)})
                except AIOutputError as exc:
                    if event.is_set():raise
                    tick(limited=True,notes=(*progress.notes,'AI 主动搜索未完成: '+str(exc)[:200]))
            else:
                tick(limited=True,notes=('本日主动搜索预算已使用，复用已收集候选',))
            budget.deadline=deadline
            tick(stage='collecting')
            if hasattr(self.sources,'collect_steps'):
                for _ in self.sources.collect_steps(scope,expansion,result,budget,event,lambda stage,count:tick(stage=stage,collected=count)):
                    tick(candidate_pool=self.search.candidate_count(scope));yield progress
            else:self.sources.collect(scope,expansion,result,budget,event,lambda stage,count:tick(stage=stage,collected=count))
            yield progress
            # Collection has its own deadline. Remaining work can consume core
            # budget until the module deadline, but never extends total duration.
            budget.deadline=deadline
            current=self.store.daily_recommendations(scope.local_date)
            own={r.repo_id for r in current if r.section==scope.section and r.keyword_id==scope.keyword_id}
            occupied={r.repo_id for r in current};seen=self.store.seen_repo_ids_before(scope.local_date)
            for search_round in range(2):
                previous_budget=getattr(self.client,'budget',None);previous_clock=getattr(self.client,'clock',None)
                if hasattr(self.client,'budget'):self.client.budget=budget;self.client.clock=self.clock
                prepared=[];pending=[];verdicts={};assessments={};daily={};cache_hits=0;unreviewed=0
                known_judgments=self.search.judgment_ids(scope,'semantic');readme_downloads=0
                try:
                    from .search_storage import _repository
                    from .search_types import ObservedRepository
                    for row,measured_day in self._frontier(scope,budget,tick,event):
                        if row is None:yield progress;continue
                        tick(stage='preparing')
                        if (len(prepared)+unreviewed)%20==0:yield progress
                        observation=ObservedRepository(_repository(json.loads(row['payload'])),row['observed_at']);repo=observation.repo
                        if repo.archived or repo.stars<scope.min_stars:continue
                        if scope.section=='keyword' and repo.id in seen:continue
                        # Old frontiers must refresh identity/Stars before today's display.
                        if datetime.fromisoformat(observation.observed_at).astimezone(self.now().tzinfo).date().isoformat()!=scope.local_date:
                            if not budget.can_spend('core',1,self.clock()):unreviewed+=1;continue
                            observation=self.sources.resolve_candidate(DiscoveryCandidate(repo.full_name,repo.id,('tracked',),observation.observed_at),budget)
                            if not observation:unreviewed+=1;continue
                            self.search.save_candidates(scope,(observation,),());repo=observation.repo
                        day=measured_day
                        if scope.section=='growth':
                            daily[repo.id]=day
                            excerpt=repo.description or 'Official GitHub daily Star statistics supplied.'
                            limited=False
                        else:
                            if not catalog_mode and progress.newly_checked+len(pending)>=cap and repo.id not in known_judgments:
                                unreviewed+=1;continue
                            if catalog_mode and readme_downloads>=max_new:
                                excerpt=None;limited=True
                            else:
                                excerpt,limited=self._readme(observation,scope,budget);readme_downloads+=1
                        evidence=tuple(self.store.source_evidence(repo.id))[:8]
                        item=PreparedCandidate(observation,AIRepositoryInput(repo,excerpt,limited,observed_at=observation.observed_at),evidence,
                            semantic_fingerprint(scope,repo,_digest(excerpt),'bounded-excerpt-v1'),evidence_fingerprint(scope,evidence,day),day)
                        if scope.section=='growth':
                            prepared.append(item);continue
                        fingerprint=item.semantic_fingerprint
                        cached=self.search.load_judgment(scope,repo.id,fingerprint,'semantic' if scope.section=='keyword' else 'evidence')
                        if cached:
                            (verdicts if scope.section=='keyword' else assessments)[repo.id]=cached;cache_hits+=1;prepared.append(item)
                        elif (catalog_mode and (continue_search or progress.catalog_calls<1)) or (not catalog_mode and progress.newly_checked+len(pending)<cap and excerpt):
                            pending.append(item);prepared.append(item)
                        else:unreviewed+=1
                    yield progress
                    if pending:
                        if catalog_mode:
                            tick(stage='checking',catalog_calls=progress.catalog_calls+1,newly_checked=progress.newly_checked+len(pending),judgment_calls=progress.judgment_calls+1)
                            results=ai_call(self.provider.filter_catalog,tuple(p.source for p in prepared),scope.term,expansion.terms if expansion else (),scope.model_id,timeout=max(.01,min(600,deadline-self.clock())))
                            verdicts.update((r.repo_id,r) for r in results)
                            self.search.save_judgments(scope,prepared,results,(),self.now().isoformat())
                            tick(checked_completed=progress.checked_completed+len(pending))
                        else:ai_call(self._judge,scope,pending,verdicts,assessments,progress,tick,timeout)
                        progress=self.search.progress(lease.job_id)
                    yield progress
                finally:
                    if hasattr(self.client,'budget'):self.client.budget=previous_budget;self.client.clock=previous_clock
                tick(stage='ranking',cache_hits=cache_hits,unique=len(prepared)+unreviewed,pending=unreviewed,
                    limited=progress.limited or self.sources.limited or bool(unreviewed),notes=tuple(dict.fromkeys((*progress.notes,*self.sources.notes,*(result.notes if result else ())))))
                current=self.store.daily_recommendations(scope.local_date)
                own={r.repo_id for r in current if r.section==scope.section and r.keyword_id==scope.keyword_id}
                occupied={r.repo_id for r in current};seen=self.store.seen_repo_ids_before(scope.local_date)
                picks=select_keyword_candidates(scope,prepared,verdicts,seen,occupied,own) if scope.section=='keyword' else select_growth_candidates(scope,prepared,daily,None,seen,occupied-own)
                enough=len([p for p in picks if p.repo_id not in seen])>=5
                if catalog_mode or search_round or enough or progress.search_calls>=2 or progress.newly_checked>=cap:
                    break
                tick(stage='searching',search_calls=progress.search_calls+1)
                known=tuple(p.observation.repo.full_name for p in prepared)[:200]
                try:
                    gap=ai_call(self.provider.search_repositories,scope,expansion,known,
                        'Too few eligible new results after deduplication and relevance checks; search other public lists and synonyms.',
                        timeout=timeout(),cancel_event=event)
                except AIOutputError as exc:
                    if event.is_set():raise
                    tick(limited=True,notes=(*progress.notes,'AI 补充搜索未完成: '+str(exc)[:200]));break
                self.search.save_cursor(scope,'ai-gap',{'date':scope.local_date,**asdict(gap)})
                result=gap
                budget.deadline=min(deadline,self.clock()+300)
                self.sources.collect(scope,expansion,gap,budget,event,lambda stage,count:tick(stage=stage,collected=count))
                budget.deadline=deadline
            if not prepared and progress.limited:
                raise AIOutputError('尚无可核实的真实候选，保留上次结果')
            coverage=GrowthCoverage(self.sources.collected,len(daily),('github_search','github_trending','trendshift','ai_web_search'),scope.stat_date,'github_daily_new',stop_reasons=progress.notes) if scope.section=='growth' else None
            observations=tuple(p.observation for p in prepared)
            snapshots=tuple(StarSnapshot(o.repo.id,scope.local_date,o.repo.stars,o.observed_at) for o in observations
                if datetime.fromisoformat(o.observed_at).astimezone(self.now().tzinfo).date().isoformat()==scope.local_date)
            tick(stage='publishing',candidate_pool=self.search.candidate_count(scope));yield progress
            while not getattr(self,'can_publish',lambda:True)():yield progress
            # Earlier modules retain their original cross-module priority.
            current=self.store.daily_recommendations(scope.local_date)
            own={r.repo_id for r in current if r.section==scope.section and r.keyword_id==scope.keyword_id}
            occupied={r.repo_id for r in current};seen=self.store.seen_repo_ids_before(scope.local_date)
            picks=select_keyword_candidates(scope,prepared,verdicts,seen,occupied,own) if scope.section=='keyword' else select_growth_candidates(scope,prepared,daily,None,seen,occupied-own)
            if not picks and progress.limited:raise AIOutputError('本轮覆盖未完成，保留上次结果')
            self.search.publish(Publication(scope,lease.generation,observations,snapshots,picks,coverage),lease,now=self.now(),cancel_event=event)
            # Cancellation after a committed publication does not undo the commit
            # or falsely report that no results were saved.
            progress=replace(progress,status='partial' if progress.limited else 'done',stage='complete',**metrics())
            self.search.save_progress(progress,lease=lease)
            if on_progress:on_progress(progress)
        except GeneratorExit:
            progress=replace(progress,status='canceled',stage='stopped',limited=True,notes=(*progress.notes,'检索已取消，断点保留'),**metrics())
            self.search.save_progress(progress,lease=lease)
            raise
        except Exception as exc:
            progress=replace(progress,status='canceled' if event.is_set() else 'paused',stage='stopped',limited=True,
                notes=tuple(dict.fromkeys((*progress.notes,str(exc)[:300]))),**metrics())
            self.search.save_progress(progress,lease=lease)
        finally:
            import logging
            logging.getLogger(__name__).info('search timing %s',json.dumps({
                'section':scope.section,'keyword_id':scope.keyword_id,'status':progress.status,
                'candidate_pool':progress.candidate_pool,'official_checked':progress.official_checked,
                **metrics()},ensure_ascii=False))
            self.search.finish_run(lease)
        return progress

    def _frontier(self,scope,budget,tick,event):
        if scope.section=='keyword':
            for row in self.search.frontier(scope):yield row,None
            return
        total=sum(1 for row in self.search.frontier(scope) if not json.loads(row['payload'])['archived'] and json.loads(row['payload'])['stars']>=scope.min_stars)
        tick(pending=total)
        measured=[];checked=0;cached_count=0;rows=iter(self.search.frontier(scope));workers=(2 if self.legacy_review else self.client.request_concurrency) if isinstance(self.client,GitHubClient) else 1
        def fetch(repo):
            def official(call,*args):
                from urllib.error import URLError,HTTPError
                from http.client import HTTPException
                for attempt in range(2):
                    if event.is_set():raise GitHubRequestError('检索已取消')
                    if getattr(self.client,'budget',None) is not budget:budget.spend('core',1,self.clock())
                    try:return call(*args)
                    except GitHubRequestError as exc:
                        # Retry one idempotent read after a dropped connection;
                        # HTTP errors, exhausted budgets and invalid data are not network retries.
                        if attempt or exc.status is not None or isinstance(exc.__cause__,HTTPError) or isinstance(exc,GitHubRateLimitError) or not isinstance(exc.__cause__,(URLError,TimeoutError,ConnectionError,HTTPException)):raise
            try:return repo,official(self.client.star_history_weeks,repo.full_name),False
            except GitHubRequestError as exc:
                if exc.status!=404 or not hasattr(self.client,'get_repository_by_id'):raise
                # A stale same-day name must not poison every future refresh.
                # Only a separate stable-ID 404 establishes current unavailability;
                # a missing statistics endpoint alone never means zero growth.
                try:current=official(self.client.get_repository_by_id,repo.id)
                except GitHubRequestError as identity_error:
                    if identity_error.status==404:return repo,None,True
                    raise
                if current.id!=repo.id:raise GitHubRequestError('官方仓库身份不一致')
                if current.archived or current.stars<scope.min_stars:return current,None,True
                return current,official(self.client.star_history_weeks,current.full_name),False
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='star-history') as pool:
            exhausted=False
            while not exhausted:
                tick(stage='measuring');batch=[]
                current_workers=min(workers,getattr(self.client,'request_concurrency',workers))
                width=current_workers if budget.can_spend('core',current_workers,self.clock()) else 1
                for _ in range(width):
                    try:row=next(rows)
                    except StopIteration:exhausted=True;break
                    from .search_storage import _repository
                    repo=_repository(json.loads(row['payload']))
                    if repo.archived or repo.stars<scope.min_stars:continue
                    cached_day=self.search.load_star_day(repo.id,scope.stat_date,self.now())
                    if cached_day is not None:
                        checked+=1;cached_count+=1;tick(official_checked=checked,cache_hits=cached_count,pending=max(0,total-checked))
                        if cached_day.added>0:measured.append((row,cached_day,repo.stars))
                        continue
                    cached=getattr(self.client,'has_cached_star_history',lambda name:False)(repo.full_name)
                    if not cached and not budget.can_spend('core',1,self.clock()):
                        tick(limited=True,notes=('官方日增核算达到请求预算，未核算候选保留断点',))
                        exhausted=True;break
                    batch.append((row,repo,pool.submit(fetch,repo)))
                for row,repo,future in batch:
                    try:
                        current,weeks,unavailable=future.result()
                        if unavailable:
                            if current==repo:self.search.mark_unavailable(repo.id,scope.local_date)
                            else:self.search.save_candidates(scope,(ObservedRepository(current,self.now().isoformat()),),())
                            total-=1;tick(pending=max(0,total-checked),notes=('仓库当前不可访问或已不符合条件，今日排除；历史保留，后续更新重新检查',))
                            continue
                        if current!=repo:
                            self.search.save_candidates(scope,(ObservedRepository(current,self.now().isoformat()),),())
                            row=dict(row,payload=json.dumps(asdict(current)),observed_at=self.now().isoformat());repo=current
                        day=next((d for d in confirmed_star_days(weeks,self.now()) if d.stat_date==scope.stat_date),None)
                        if day is None:
                            tick(limited=True,notes=('官方统计日或UTC边界无法验证，未当作零日增',))
                        else:
                            self.search.save_star_day(repo.id,day,self.now());checked+=1;tick(official_checked=checked,pending=max(0,total-checked))
                        self.store.mark_catalog_scored(repo.id,self.now().isoformat())
                        if day and day.added>0:measured.append((row,day,repo.stars))
                    except (GitHubRequestError,ValueError,OSError) as exc:
                        if isinstance(exc,GitHubRateLimitError):exhausted=True
                        tick(limited=True,notes=(str(exc)[:300],))
                yield None,None
        measured.sort(key=lambda item:(-item[1].added,-item[2],item[0]['repo_id']))
        for row,day,_ in measured:yield row,day

    def _judge(self,scope,batch,verdicts,assessments,progress,tick,timeout):
        # Reserve usage before launching so a failed or interrupted invocation
        # cannot reset the same-day budget by repeatedly pressing Update.
        tick(stage='checking',newly_checked=progress.newly_checked+len(batch),judgment_calls=progress.judgment_calls+1)
        if scope.section=='keyword':
            results=self.provider.filter_batch(tuple(p.source for p in batch),scope.term,scope.model_id,timeout=timeout())
            verdicts.update((r.repo_id,r) for r in results)
            self.search.save_judgments(scope,batch,results,(),self.now().isoformat())
            tick(checked_completed=progress.checked_completed+len(batch))
        else:
            results=self.provider.assess_growth_batch(tuple(batch),scope,timeout=timeout())
            assessments.update((r.repo_id,r) for r in results)
            self.search.save_judgments(scope,batch,(),results,self.now().isoformat())
