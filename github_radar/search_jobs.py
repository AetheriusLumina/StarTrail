"""Shared automatic search entry for browser, CLI and Windows scheduler."""
import threading
import uuid
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

    def cancel(self):
        self._event.set()
        if self._active:self._active.provider.cancel()

    def continue_keyword(self,keyword_id,day,*,model_id=None):
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
        try:
            scope=SearchScope('keyword',rule.id,day,None,rule.term,rule.min_stars,model)
            self._active=self.factory()
            progress=self._active.run(scope,max_new=200,continue_search=True,cancel_event=self._event)
            if progress.status not in ('done','partial'):raise ValueError('；'.join(progress.notes) or '检索未完成，原结果保留')
            return progress
        finally:self._active=None;search.finish_run(lease)

    def refresh(self,day,observed_at):
        if not self.ready():return None  # Public GitHub fallback remains available.
        now=self.now();model=self.model()
        scope=SearchScope('growth',None,day,(now.astimezone(timezone.utc).date()-timedelta(days=1)).isoformat(),'',100,model)
        search=SearchStore(self.store)
        lease=search.claim_run(scope,self.owner,namespace='refresh',now=now.timestamp(),ttl=1900)
        if not lease.acquired:
            return replace(self.service.load_latest(day),status='cached',message='已有更新正在进行，复用当前任务')
        self._event=threading.Event();self.progress=[]
        scopes=[scope]+[SearchScope('keyword',r.id,day,None,r.term,r.min_stars,model) for r in self.store.list_keywords() if r.enabled]
        heartbeat_stop=threading.Event()
        def heartbeat():
            while not heartbeat_stop.wait(30):
                if not search.renew_run(lease,now=self.now().timestamp(),ttl=1900):self.cancel();return
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        try:
            for module in scopes:
                if self._event.is_set():break
                self._active=self.factory()
                p=self._active.run(module,cancel_event=self._event)
                self.progress.append(p)
                search.renew_run(lease,now=self.now().timestamp(),ttl=1900)
        finally:
            heartbeat_stop.set();thread.join(timeout=1)
            self._active=None;search.finish_run(lease)
        successful=any(p.status in ('done','partial') for p in self.progress)
        partial=any(p.status!='done' for p in self.progress)
        notes=tuple(dict.fromkeys(note for p in self.progress for note in p.notes))
        result=self.service._result(day,self.store.daily_recommendations(day),'ok' if successful else 'error',
            not successful,'已更新；部分检索仍待继续' if successful and partial else '更新已完成' if successful else '检索未完成，已保留上次结果',notes)
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
