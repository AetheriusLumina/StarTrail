"""Shared automatic search entry for browser, CLI and Windows scheduler."""
import threading
import uuid
import logging
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from .search_storage import SearchStore
from .search_types import SearchScope

class SearchJobs:
    def __init__(self,service,store,coordinator_factory,*,ready=lambda:True,model=None,now=None):
        self.service,self.store,self.factory=service,store,coordinator_factory
        self.ready=ready;self.model=model or store.load_ai_active_model
        self.now=now or (lambda:datetime.now().astimezone())
        self._event=threading.Event();self._active=None;self._guard=threading.Lock()
        self.owner=uuid.uuid4().hex;self.progress=[]

    def _renew_leases(self,search,lease):
        if not search.renew_run(lease,now=self.now().timestamp(),ttl=1900):return False
        engines=list(getattr(self,'_engines',()))
        if self._active is not None and self._active not in engines:engines.append(self._active)
        for engine in engines:
            module=getattr(engine,'current_lease',None)
            if module is not None and not search.renew_run(module,now=self.now().timestamp(),ttl=1900):
                if getattr(engine,'current_lease',None)==module:return False
        return True

    def _start_heartbeat(self,search,lease):
        stop=threading.Event()
        def heartbeat():
            while not stop.wait(30):
                if not self.store.load_search_enabled() or not self._renew_leases(search,lease):self.cancel();return
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        return stop,thread

    def cancel(self):
        self._event.set()
        for engine in getattr(self,'_engines',()):engine.provider.cancel()
        if self._active:self._active.provider.cancel()

    def prepare(self,cancel_event=None):
        """Awake-only public fact preparation; never spends AI or publishes cards."""
        self._event=cancel_event or threading.Event()
        if not self.store.load_search_enabled():return
        now=self.now();day=now.date().isoformat();model=self.store.load_ai_active_model()
        scope=SearchScope('growth',None,day,(now.astimezone(timezone.utc).date()-timedelta(days=1)).isoformat(),'',100,model)
        search=SearchStore(self.store);lease=search.claim_run(scope,self.owner,namespace='refresh',now=now.timestamp(),ttl=700)
        if not lease.acquired:return
        deadline=self.service.clock()+600
        try:
            scopes=[scope]+[SearchScope('keyword',r.id,day,None,r.term,r.min_stars,model) for r in self.store.list_keywords() if r.enabled]
            engines=[self.factory() for _ in scopes];self._engines=engines
            iterators=[e.run_steps(s,prepare_only=True,budget_seconds=max(1,deadline-self.service.clock()),cancel_event=self._event) for e,s in zip(engines,scopes)]
            done=set()
            try:
                while len(done)<len(iterators) and not self._event.is_set() and self.service.clock()<deadline:
                    for i,(e,it) in enumerate(zip(engines,iterators)):
                        if i in done or self._event.is_set():continue
                        self._active=e
                        try:next(it)
                        except StopIteration:done.add(i)
                    search.renew_run(lease,now=self.now().timestamp(),ttl=700)
            finally:
                for it in iterators:it.close()
        finally:
            self._active=None;self._engines=[];search.finish_run(lease)

    def continue_keyword(self,keyword_id,day,*,model_id=None):
        # A user continuation is cancellable search even though it uses the AI worker.
        self.foreground_active=True;self.foreground_progress=[]
        started=self.service.clock();progress=None
        try:
            progress=self._continued_keyword(keyword_id,day,model_id=model_id)
            return progress
        finally:
            self.foreground_active=False;self.foreground_progress=[]
            published_at=self.store.daily_updated_at(day) if progress is not None else None
            timing={'operation':'continue_keyword','elapsed_seconds':round(max(0,self.service.clock()-started),3),
                    'ai_seconds':progress.ai_seconds if progress else 0,
                    'stage_seconds':progress.stage_seconds if progress else {},'status':'ok' if progress else 'error',
                    'attempted_at':self.now().isoformat(),'published_at':published_at,
                    'quota_scoped':bool(progress and published_at and progress.status=='partial' and progress.quota_limited)}
            from contextlib import closing
            with closing(self.store._connect()) as db,db:
                db.execute("INSERT INTO settings(name,value) VALUES('search_refresh_timing',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(timing),))
            logging.getLogger(__name__).info('continuation timing %s',json.dumps(timing))

    def _continued_keyword(self,keyword_id,day,*,model_id=None):
        if not self.ready():raise ValueError('请先连接Codex再继续深度检索')
        now=self.now()
        if day!=now.date().isoformat():raise ValueError('深度检索只更新今天的结果，历史记录保留')
        rule=next((r for r in self.store.list_keywords() if r.id==keyword_id and r.enabled),None)
        if rule is None:raise ValueError('关键词已变化，请刷新后重试')
        model=model_id or self.model()
        global_scope=SearchScope('growth',None,day,(now.astimezone(timezone.utc).date()-timedelta(days=1)).isoformat(),'',100,model)
        search=SearchStore(self.store)
        lease=search.claim_run(global_scope,self.owner,namespace='refresh',now=now.timestamp(),ttl=1900)
        if not lease.acquired:raise ValueError('已有更新正在进行，请完成后再继续')
        self._event=threading.Event()
        heartbeat_stop,thread=self._start_heartbeat(search,lease)
        try:
            scope=SearchScope('keyword',rule.id,day,None,rule.term,rule.min_stars,model)
            self._active=self.factory()
            progress=self._active.run(scope,max_new=200,continue_search=True,cancel_event=self._event,on_progress=lambda p:setattr(self,'foreground_progress',[p]))
            if progress.status not in ('done','partial'):raise ValueError('；'.join(progress.notes) or '检索未完成，原结果保留')
            return progress
        finally:
            heartbeat_stop.set();thread.join(timeout=1)
            self._active=None;search.finish_run(lease)

    def refresh(self,day,observed_at):
        # Live front-end progress is linked to this invocation, never disk leftovers.
        self.foreground_active=True;self.foreground_progress=[]
        try:return self._recorded_refresh(day,observed_at)
        finally:self.foreground_active=False;self.foreground_progress=[]

    def _recorded_refresh(self,day,observed_at):
        """Record the whole wall clock, including readiness, AI and failed work."""
        started=self.service.clock();self.progress=[]
        result=None
        try:
            result=self._refresh(day,observed_at)
        finally:
            if result is not None or self.progress:
                elapsed=round(max(0,self.service.clock()-started),3)
                ai_seconds=round(sum(p.ai_seconds for p in self.progress),3)
                stages={}
                for progress in self.progress:
                    for stage,seconds in progress.stage_seconds.items():stages[stage]=round(stages.get(stage,0)+seconds,3)
                timing={'elapsed_seconds':elapsed,'ai_seconds':ai_seconds,'stage_seconds':stages,'status':result.status if result else 'error','attempted_at':self.now().isoformat()}
                timing['quota_scoped']=bool(result and result.status=='ok' and any(p.status=='partial' and p.quota_limited for p in self.progress))
                timing['published_at']=self.store.daily_updated_at(day) if result and result.status=='ok' else None
                from contextlib import closing
                with closing(self.store._connect()) as db,db:
                    db.execute("INSERT INTO settings(name,value) VALUES('search_refresh_timing',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(json.dumps(timing),))
                logging.getLogger(__name__).info('refresh timing %s',json.dumps(timing))
                if result is not None:
                    # Return through the ordinary result path below, not finally:
                    # exceptions and cancellation must keep their original meaning.
                    result=replace(result,elapsed_seconds=elapsed,ai_seconds=ai_seconds,stage_seconds=stages)
        return result

    def _refresh(self,day,observed_at):
        if not self.store.load_search_enabled() or not self.ready():return None  # Public GitHub fallback remains available.
        started=self.service.clock()
        now=self.now();model=self.model()
        scope=SearchScope('growth',None,day,(now.astimezone(timezone.utc).date()-timedelta(days=1)).isoformat(),'',100,model)
        search=SearchStore(self.store)
        lease=search.claim_run(scope,self.owner,namespace='refresh',now=now.timestamp(),ttl=1900)
        if not lease.acquired:
            return replace(self.service.load_latest(day),status='cached',message='已有更新正在进行，复用当前任务')
        self._event=threading.Event();self.progress=[]
        scopes=[scope]+[SearchScope('keyword',r.id,day,None,r.term,r.min_stars,model) for r in self.store.list_keywords() if r.enabled]
        heartbeat_stop,thread=self._start_heartbeat(search,lease)
        try:
            if not self.store.mark_search_incomplete(day, True, lease=lease, now=self.now().timestamp()):
                raise ValueError('当前更新已被其他任务替代，已保存结果保留')
            engines=[self.factory() for _ in scopes];iterators=[];latest=[None]*len(scopes);done=[False]*len(scopes)
            from .search_coordinator import SearchCoordinator
            atomic=all(isinstance(engine,SearchCoordinator) and not engine.legacy_review for engine in engines)
            ai_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='ai-discovery') if atomic else None
            self._engines=engines
            shared_pages={}
            for engine in engines:
                if hasattr(engine,'sources'):engine.sources.shared_pages=shared_pages
            if ai_pool:
                for engine in engines:engine.ai_executor=ai_pool
            prior_cache=getattr(self.service.client,'request_cache',None)
            from .github_client import GitHubClient
            from .search_storage import SearchRequestCache
            if isinstance(self.service.client,GitHubClient):self.service.client.request_cache=SearchRequestCache(self.store)
            try:
                for index,(engine,module) in enumerate(zip(engines,scopes)):
                    engine.can_publish=lambda index=index:all(done[:index])
                    if hasattr(engine,'run_steps'):iterators.append(engine.run_steps(module,cancel_event=self._event,**({'defer_publish':True} if atomic else {})))
                    else:
                        def compatibility(engine=engine,module=module):
                            if False:yield None
                            return engine.run(module,cancel_event=self._event)
                        iterators.append(compatibility())
                while not all(done):
                    if self._event.is_set() or not self.store.load_search_enabled():break
                    for index,(engine,iterator) in enumerate(zip(engines,iterators)):
                        if self._event.is_set() or not self.store.load_search_enabled():break
                        if done[index]:continue
                        if latest[index] is not None and latest[index].stage=='publishing' and not engine.can_publish():continue
                        self._active=engine
                        try:latest[index]=next(iterator)
                        except StopIteration as complete:latest[index]=complete.value;done[index]=True
                        self.foreground_progress=[p for p in latest if p is not None]
                    self._renew_leases(search,lease)
                    if any(p and p.ai_started_at and not done[i] for i,p in enumerate(latest)):time.sleep(.05)
                self.progress=[p for p,complete in zip(latest,done) if p is not None and complete]
                if atomic and all(done) and all(p is not None and p.status=='ready' for p in latest) and not self._event.is_set() and self.store.load_search_enabled():
                    targets={(module.section,module.keyword_id) for module in scopes}
                    occupied={r.repo_id for r in self.store.daily_recommendations(day) if (r.section,r.keyword_id) not in targets}
                    values=[];leases=[]
                    for engine in engines:
                        value,module_lease=engine.deferred_publication
                        picks=engine.rerank(occupied)
                        values.append(replace(value,recommendations=picks));leases.append(module_lease)
                        occupied.update(r.repo_id for r in picks)
                    try:
                        search.publish_all(tuple(values),tuple(leases),now=self.now(),cancel_event=self._event,refresh_lease=lease)
                    except Exception as exc:
                        self.progress=[replace(p,status='paused',stage='stopped',limited=True,notes=(*p.notes,str(exc)[:300])) for p in self.progress]
                    else:self.progress=[replace(p,status='partial' if p.quota_limited else 'done',stage='complete') for p in self.progress]
                    for progress,module_lease in zip(self.progress,leases):search.save_progress(progress,lease=module_lease)
                elif atomic:
                    self.progress=[replace(p,status='paused' if p.status=='ready' else p.status,stage='stopped' if p.status=='ready' else p.stage,limited=True,notes=(*p.notes,'另一榜单未完成，整份旧榜保留') if p.status=='ready' else p.notes) for p in self.progress]
                    for engine,progress in zip(engines,self.progress):
                        deferred=getattr(engine,'deferred_publication',None)
                        if deferred:search.save_progress(progress,lease=deferred[1])
            finally:
                for iterator in iterators:iterator.close()
                if ai_pool:
                    if self._event.is_set():
                        for engine in engines:engine.provider.cancel()
                    ai_pool.shutdown(wait=True,cancel_futures=True)
                self._engines=[]
                if atomic:
                    self.progress=[search.progress(p.job_id) for p in latest if p is not None]
                for engine in engines:
                    deferred=getattr(engine,'deferred_publication',None)
                    if deferred:search.finish_run(deferred[1])
                if isinstance(self.service.client,GitHubClient):self.service.client.request_cache=prior_cache
            interrupted=len(self.progress)<len(scopes) or any(p.status not in ('done','partial') for p in self.progress)
            if not self.store.mark_search_incomplete(day, interrupted, lease=lease, now=self.now().timestamp()):
                raise ValueError('当前更新已被其他任务替代，已保存结果保留')
        finally:
            heartbeat_stop.set();thread.join(timeout=1)
            self._active=None;search.finish_run(lease)
        successful=any(p.status in ('done','partial') for p in self.progress)
        partial=len(self.progress)<len(scopes) or any(p.status!='done' for p in self.progress)
        notes=tuple(dict.fromkeys(note for p in self.progress for note in p.notes))
        if len(self.progress)<len(scopes):notes+=('检索提前停止，未执行的模块尚未更新；已保存结果保留',)
        interrupted=len(self.progress)<len(scopes) or any(p.status not in ('done','partial') for p in self.progress)
        failed_notes=tuple(note for p in self.progress if p.status not in ('done','partial') for note in p.notes)
        failure_detail='；'.join((failed_notes or notes)[:3])[:500]
        failure_message='检索未完成，已保留上次结果'+('：'+failure_detail if failure_detail else '')
        result=self.service._result(day,self.store.daily_recommendations(day),'partial' if successful and interrupted else 'ok' if successful else 'error',
            not successful,('部分更新未完成，已保留已保存结果：'+failure_detail) if successful and interrupted else '本轮额度范围更新成功；未处理候选和检索断点保留，下次更新继续' if successful and partial else '更新已完成' if successful else failure_message,notes)
        if atomic and not successful:
            old=self.service.load_latest(day)
            result=replace(old,status='error',stale=True,message=failure_message,notes=notes)
        return result

def install_search(service,connection=None):
    from .codex_connection import CodexConnection
    from .ai_provider import CodexProvider
    from .search_provider import SearchProvider
    from .search_coordinator import SearchCoordinator
    from .search_sources import SearchSources
    from .trendshift import TrendshiftClient
    connection=connection or CodexConnection()
    def factory():
        sources=SearchSources(service.client,service.store,service.trending,
            service.discovery.trendshift if service.discovery else TrendshiftClient(),service.clock,
            free_events=service.discovery.free_events if service.discovery else None)
        return SearchCoordinator(service.store,service.client,SearchProvider(CodexProvider(connection)),sources=sources,clock=service.clock)
    def model():
        selected=CodexProvider(connection)._selected_model(service.store.load_ai_model() or service.store.load_ai_active_model())
        service.store.save_ai_active_model(selected)
        return selected
    service.search_jobs=SearchJobs(service,service.store,factory,ready=lambda:connection.probe().ready,model=model)
    return service.search_jobs
