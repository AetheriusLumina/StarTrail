"""Collect official metadata and public evidence with resumable, bounded queries."""
import json
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date,datetime,timedelta
from .github_client import GitHubClient, GitHubRequestError
from .public_http import SourceRequestError
from .search_storage import SearchStore,scope_key,_repository
from .search_types import ObservedRepository
from .discovery_types import DiscoveryBatch,DiscoveryCandidate,SourceEvidence,valid_repository_name

def literal_term(term):
    # Quote user values, keeping operators and quotes out of the query grammar.
    return '"'+term.replace('\\','\\\\').replace('"','\\"')+'"'

class SearchSources:
    def __init__(self,client,store,trending,trendshift,clock,free_events=None):
        self.client,self.store,self.trending,self.trendshift,self.clock=client,store,trending,trendshift,clock
        self.free_events=free_events
        self.metadata_workers=4
        self.search=SearchStore(store);self.notes=[];self.limited=False;self.collected=0

    def _page(self,key,call):
        cache=getattr(self,'shared_pages',None)
        if cache is not None and key in cache:return cache[key]
        value=call()
        if cache is not None:cache[key]=value
        return value

    def _source_batch(self,batch):
        # Supplementary feeds expose a finite public window. An unfinished
        # site catalog is reported as partial, not mislabeled as a failed GET.
        self.source_status[batch.source_name]={'status':'complete' if batch.batch_finished else 'partial' if batch.candidates else 'failed','count':len(batch.candidates)}
        self.notes.extend(batch.notes)
        if not batch.batch_finished and not batch.notes:self.notes.append(batch.source_name+'：来源覆盖未完整，未声明全站完成')

    def _call(self,budget,resource,func,*args,**kwargs):
        if getattr(self.client,'budget',None) is not budget:budget.spend(resource,1,self.clock())
        return func(*args,**kwargs)

    def _save_cursor(self,scope,term,queue):
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT INTO search_cursors VALUES(?,?,?) ON CONFLICT(scope_key,source) DO UPDATE SET payload=excluded.payload',
                (scope_key(scope),'github:'+term+(':name-topics-v1' if getattr(self,'strict_keyword',False) else ''),json.dumps({'date':scope.local_date,'queue':queue})))

    def _queue(self,scope,term,start,end):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM search_cursors WHERE scope_key=? AND source=?',
                (scope_key(scope),'github:'+term+(':name-topics-v1' if getattr(self,'strict_keyword',False) else ''))).fetchone()
        saved=json.loads(row[0]) if row else None
        fresh=[{'start':start.isoformat(),'end':end.isoformat(),'low':scope.min_stars,'high':None,'page':1}]
        if not saved:return fresh
        # A new date reopens page one as well as unfinished ranges: new projects
        # and recently changed Stars are not hidden behind yesterday's cursor.
        if saved['queue']:
            if saved['date']!=scope.local_date:
                # Prioritize newly uncovered dates without restarting the
                # entire all-age prefix ahead of an unfinished frontier.
                new_start=max(start,date.fromisoformat(saved['date'])+timedelta(days=1))
                tail=[dict(fresh[0],start=new_start.isoformat())] if new_start<=end else []
                return tail+saved['queue']
            return saved['queue']
        return fresh if saved['date']!=scope.local_date else []

    def _search_range(self,scope,term,start,end,budget,cancel_event,on_progress):
        for _ in self._search_range_steps(scope,term,start,end,budget,cancel_event,on_progress):pass

    def _search_range_steps(self,scope,term,start,end,budget,cancel_event,on_progress):
        # Keep the daily growth lane independent of the persistent catalog walk.
        cursor_term=term+':daily-growth' if scope.section=='growth' and getattr(self,'daily_discovery',False) else term
        queue=self._queue(scope,cursor_term,start,end)
        while queue and not cancel_event.is_set() and budget.can_spend('search',1,self.clock()):
            item=queue[0];stars=f"stars:>={item['low']}" if item['high'] is None else f"stars:{item['low']}..{item['high']}"
            strict=getattr(self,'strict_keyword',False)
            base=literal_term(term)+(' in:name,topics' if strict else ' in:name,description,readme') if term else ''
            query=f"{base} archived:false {stars} created:{item['start']}..{item['end']}"
            try:page=self._call(budget,'search',self.client.search_page,query,page=item['page'],per_page=100,sort='stars')
            except (GitHubRequestError,ValueError,OSError) as exc:
                self.notes.append(str(exc));self.limited=True;break
            observed=datetime.now().astimezone().isoformat()
            self.search.save_candidates(scope,tuple(ObservedRepository(r,observed) for r in page.items),())
            if scope.section=='keyword':
                for repo in page.items:self.search.save_match(scope,repo,term,'github_name_topic' if strict else 'github_query',observed)
            for repo in page.items:self.search.mark_available(repo.id)
            self.collected+=len(page.items);on_progress('collecting',self.collected)
            first,last=date.fromisoformat(item['start']),date.fromisoformat(item['end'])
            if page.total_count>1000 and first<last:
                middle=first+(last-first)//2
                queue[:1]=[dict(item,end=middle.isoformat(),page=1),dict(item,start=(middle+timedelta(days=1)).isoformat(),page=1)]
            elif page.total_count>1000 and any(r.stars>item['low'] for r in page.items):
                high=item['high'] if item['high'] is not None else max(r.stars for r in page.items)
                middle=item['low']+(high-item['low'])//2
                queue[:1]=[dict(item,high=middle,page=1),dict(item,low=middle+1,page=1)]
            elif page.incomplete_results:
                self.notes.append('GitHub 搜索结果标为不完整，保留断点待继续');self.limited=True;break
            else:
                if page.total_count>1000:
                    self.notes.append('相同日期和Star的分区仍超过1000项，公开接口覆盖有限');self.limited=True
                if item['page']*100<min(page.total_count,1000) and len(page.items)==100:queue[0]=dict(item,page=item['page']+1)
                else:queue.pop(0)
            self._save_cursor(scope,cursor_term,queue)
            yield None
        if queue:self.limited=True
        self._save_cursor(scope,cursor_term,queue)

    def _missing(self,identity,exc):
        if getattr(exc,'status',None)!=404:return False
        if identity:self.search.mark_unavailable(identity,datetime.now().astimezone().date().isoformat())
        self.notes.append('仓库未找到或无访问权限（HTTP 404），今天不推荐，下一天重新检查；历史保留')
        return True

    @staticmethod
    def _ai_citation_matches(candidate):
        from urllib.parse import urlsplit,unquote
        for evidence in candidate.evidence:
            url=urlsplit(evidence.source_url)
            if url.scheme=='https' and url.hostname=='github.com' and not url.username and not url.password:
                parts=[unquote(p) for p in url.path.strip('/').split('/')]
                if len(parts)>=2 and '/'.join(parts[:2]).casefold()==candidate.full_name.casefold():return True
        return False

    def resolve_candidate(self,candidate,budget):
        if not valid_repository_name(candidate.full_name):return None
        try:
            try:repo=self._call(budget,'core',self.client.get_repository,candidate.full_name)
            except GitHubRequestError as exc:
                if getattr(exc,'status',None)!=404 or not candidate.repo_id:raise
                repo=self._call(budget,'core',self.client.get_repository_by_id,candidate.repo_id)
            if candidate.repo_id is not None and repo.id!=candidate.repo_id:
                repo=self._call(budget,'core',self.client.get_repository_by_id,candidate.repo_id)
            return self._accept_candidate(candidate,repo)
        except (GitHubRequestError,ValueError,OSError) as exc:
            if not self._missing(candidate.repo_id,exc):self.notes.append(str(exc));self.limited=True
            return None

    def _accept_candidate(self,candidate,repo):
        if candidate.repo_id is not None and candidate.repo_id!=repo.id:
            self.notes.append('候选仓库身份变化，未覆盖原ID');return None
        self.search.mark_available(repo.id)
        observed=datetime.now().astimezone().isoformat()
        evidence=tuple(replace(e,repo_id=repo.id,full_name=repo.full_name) for e in candidate.evidence)
        self.store.save_discovery_batch(DiscoveryBatch('verified',
            (replace(candidate,repo_id=repo.id,full_name=repo.full_name,cached_repo=repo,evidence=evidence),),None,True,()))
        return ObservedRepository(repo,observed)

    def _collect_metadata_steps(self,candidates,scope,budget,cancel_event,on_progress):
        # Only network waits overlap. Evidence and SQLite writes remain ordered
        # on this thread, with at most four requests/results in memory.
        resolved={};index=0
        workers=min(4,max(1,self.metadata_workers)) if isinstance(self.client,GitHubClient) else 1
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='metadata') as pool:
            while index<len(candidates):
                if cancel_event.is_set():self.limited=True;break
                candidate=candidates[index];key=candidate.full_name.casefold()
                known=resolved.get(key) or self.search.observation_named_today(scope,candidate.full_name)
                if known and (candidate.repo_id is None or candidate.repo_id==known.repo.id):
                    self._accept_candidate(candidate,known.repo)
                    if scope.section=='keyword' and key in getattr(self,'_ai_names',set()):self.search.save_match(scope,known.repo,scope.term,'ai_search',known.observed_at)
                    resolved[key]=known;index+=1;continue
                if not budget.can_spend('core',1,self.clock()):
                    self.notes.append('GitHub 元数据请求额度或时间预算不足，未核实候选已保留；额度恢复后可继续更新')
                    self.limited=True;break
                width=workers if budget.can_spend('core',workers,self.clock()) else 1
                batch=[];keys=set()
                while index<len(candidates) and len(batch)<width:
                    candidate=candidates[index];key=candidate.full_name.casefold()
                    if key in keys or key in resolved:break
                    index+=1
                    if not valid_repository_name(candidate.full_name):continue
                    keys.add(key)
                    batch.append((candidate,pool.submit(self._call,budget,'core',self.client.get_repository,candidate.full_name)))
                for candidate,future in batch:
                    if cancel_event.is_set():self.limited=True;break
                    try:
                        try:repo=future.result()
                        except GitHubRequestError as exc:
                            if getattr(exc,'status',None)!=404 or not candidate.repo_id:raise
                            repo=self._call(budget,'core',self.client.get_repository_by_id,candidate.repo_id)
                        if candidate.repo_id is not None and repo.id!=candidate.repo_id:
                            repo=self._call(budget,'core',self.client.get_repository_by_id,candidate.repo_id)
                        observation=self._accept_candidate(candidate,repo)
                        if observation is None:self.limited=True
                    except (GitHubRequestError,ValueError,OSError) as exc:
                        if getattr(exc,'status',None)==404:
                            self.notes.append('仓库 '+candidate.full_name+' 未找到或无访问权限（HTTP 404），已跳过，其他来源继续')
                        else:self.notes.append(str(exc));self.limited=True
                        continue
                    if observation:
                        if scope.section=='keyword' and candidate.full_name.casefold() in getattr(self,'_ai_names',set()):self.search.save_match(scope,observation.repo,scope.term,'ai_search',observation.observed_at)
                        resolved[candidate.full_name.casefold()]=observation
                        self.search.save_candidates(scope,(observation,),())
                        self.collected+=1;on_progress('collecting',self.collected)
                yield None

    def refresh_candidates_steps(self,scope,budget,cancel_event,on_progress):
        """Refresh stale durable metadata in bounded official batches."""
        batch=[]
        def resolve(rows, supplied=None):
            names=tuple(repo.full_name for repo in rows)
            if isinstance(self.client,GitHubClient) and self.client._authorization():
                try:resolved=supplied.result() if supplied is not None else self.client.get_repositories_batch(names)
                except (GitHubRequestError,ValueError,OSError) as exc:
                    self.notes.append(str(exc));self.limited=True;return
                for repo in rows:
                    updated=resolved.get(repo.full_name.casefold())
                    if updated is None or updated.id!=repo.id:
                        # A stable ID can survive rename/transfer. Never attach
                        # a new repository occupying the old name to this ID.
                        try:updated=self._call(budget,'core',self.client.get_repository_by_id,repo.id)
                        except (GitHubRequestError,ValueError,OSError) as exc:
                            if not self._missing(repo.id,exc):self.notes.append(str(exc));self.limited=True
                            continue
                    candidate=DiscoveryCandidate(repo.full_name,repo.id,('tracked',),datetime.now().astimezone().isoformat())
                    item=self._accept_candidate(candidate,updated)
                    if item:self.search.save_candidates(scope,(item,),())
                    else:self.limited=True
            else:
                for repo in rows:
                    item=self.resolve_candidate(DiscoveryCandidate(repo.full_name,repo.id,('tracked',),datetime.now().astimezone().isoformat()),budget)
                    if item:self.search.save_candidates(scope,(item,),())
                    elif not self.search.unavailable(repo.id,scope.local_date):self.limited=True
        parallel=isinstance(self.client,GitHubClient) and bool(self.client._authorization())
        workers=min(4,max(1,self.metadata_workers)) if parallel else 1
        def flush(rows,pool):
            chunks=[rows[i:i+20] for i in range(0,len(rows),20)]
            pending=[pool.submit(self.client.get_repositories_batch,tuple(repo.full_name for repo in chunk)) for chunk in chunks] if parallel else [None]*len(chunks)
            for chunk,future in zip(chunks,pending):
                if cancel_event.is_set():
                    for job in pending:
                        if job is not None:job.cancel()
                    return
                resolve(chunk,future);on_progress('collecting',self.collected)
        # At most four official requests overlap. Each still carries twenty
        # repositories; all evidence and SQLite writes stay on this thread.
        with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='official-batch') as pool:
            for row in self.search.frontier(scope):
                if cancel_event.is_set():return
                if datetime.fromisoformat(row['observed_at']).astimezone().date().isoformat()==scope.local_date:continue
                if not budget.can_spend('external',1,self.clock()):self.limited=True;return
                batch.append(_repository(json.loads(row['payload'])))
                if len(batch)==20*workers:
                    flush(batch,pool);batch=[];yield None
            if batch:flush(batch,pool);yield None

    def collect(self,scope,expansion,ai_result,budget,cancel_event,on_progress):
        for _ in self.collect_steps(scope,expansion,ai_result,budget,cancel_event,on_progress):pass

    def collect_steps(self,scope,expansion,ai_result,budget,cancel_event,on_progress):
        self.notes=[];self.limited=False;self.collected=0;self.source_status={}
        prior={name:getattr(self.client,name,None) for name in ('budget','clock')}
        if hasattr(self.client,'budget'):self.client.budget=budget;self.client.clock=self.clock
        candidates=list(ai_result.candidates) if ai_result else []
        self._ai_names={c.full_name.casefold() for c in candidates if self._ai_citation_matches(c)}
        try:
            # Trend pages are evidence lanes, not a membership requirement.
            if self.trendshift and budget.can_spend('external',1,self.clock()):
                self.trendshift.budget=budget;self.trendshift.clock=self.clock
                try:
                    batches=list(self._page(('trendshift_daily',),lambda:self.trendshift.daily(datetime.now().astimezone().isoformat())))
                    for topic in (expansion.topics if expansion else ()):
                        if cancel_event.is_set() or not budget.can_spend('external',1,self.clock()):break
                        batches.append(self._page(('trendshift_topic',topic),lambda topic=topic:self.trendshift.topic(topic,datetime.now().astimezone().isoformat())))
                    for batch in batches:
                        candidates.extend(batch.candidates);self._source_batch(batch)
                except (SourceRequestError,ValueError,OSError) as exc:self.notes.append('Trendshift: '+str(exc));self.source_status['trendshift']={'status':'failed','count':0}
            if self.trending and budget.can_spend('external',1,self.clock()):
                try:
                    names=self._page(('github_trending',),self.trending.repo_names)
                    candidates.extend(DiscoveryCandidate(n,None,('github_trending',),datetime.now().astimezone().isoformat()) for n in names)
                    self.source_status['github_trending']={'status':'complete','count':len(names)}
                except Exception as exc:self.notes.append('GitHub Trending: '+str(exc));self.source_status['github_trending']={'status':'failed','count':0}
            if scope.section=='growth':
                tracked=[r for r,_,_ in self.store.followed_repositories()]+self.store.recent_growth_repositories(scope.local_date,limit=200)
                candidates.extend(DiscoveryCandidate(r.full_name,r.id,('tracked',),datetime.now().astimezone().isoformat()) for r in tracked)
                # Full catalog is paged below. Never silently truncate resolved IDs.
                with closing(self.store._connect()) as db:
                    after=0
                    while True:
                        rows=db.execute("SELECT payload,observed_at,repo_id FROM search_candidates WHERE json_extract(scope_key,'$[0]')='growth' AND repo_id>? ORDER BY repo_id LIMIT 100",(after,)).fetchall()
                        if not rows:break
                        self.search.save_candidates(scope,tuple(ObservedRepository(_repository(json.loads(r['payload'])),r['observed_at']) for r in rows),())
                        after=rows[-1]['repo_id']
                if self.free_events and budget.can_spend('external',1,self.clock()):
                    self.free_events.budget=budget;self.free_events.clock=self.clock
                    try:
                        batch=self.free_events.discover(scope.stat_date,self.store.load_discovery_cursor('public_activity'))
                        self.store.save_discovery_batch(batch);candidates.extend(batch.candidates)
                        self._source_batch(batch)
                    except (SourceRequestError,ValueError,OSError) as exc:
                        self.notes.append('public_activity: '+str(exc));self.source_status['public_activity']={'status':'failed','count':0}
            yield None
            terms=(scope.term,*(expansion.terms if expansion else ())) if scope.section=='keyword' else ('',)
            # Daily growth keeps the established recent-discovery window. Old
            # IDs are still refreshed and ranked from the durable full catalog.
            # Awake-only preparation resumes the all-age discovery cursor.
            start=date.fromisoformat(scope.local_date)-timedelta(days=30) if scope.section=='growth' and getattr(self,'daily_discovery',False) else date(2007,1,1)
            for term in dict.fromkeys(terms):
                if cancel_event.is_set() or not budget.can_spend('search',1,self.clock()):
                    if not cancel_event.is_set():self.notes.append('GitHub 搜索请求额度或时间预算不足，检索断点已保留；额度恢复后可继续更新')
                    self.limited=True;break
                yield from self._search_range_steps(scope,term,start,date.fromisoformat(scope.local_date),budget,cancel_event,on_progress)
            yield from self._collect_metadata_steps(candidates,scope,budget,cancel_event,on_progress)
            if scope.section=='growth':
                after=''
                while not cancel_event.is_set():
                    with closing(self.store._connect()) as db:
                        rows=db.execute('SELECT * FROM discovery_catalog WHERE catalog_key>? ORDER BY catalog_key LIMIT 100',(after,)).fetchall()
                    if not rows:break
                    unresolved=[]
                    for row in rows:
                        if row['cached_repo'] and row['repo_id']:
                            # Catalog provenance is not a metadata fetch timestamp.
                            # Force an official batch refresh unless this round has it.
                            repo=_repository(json.loads(row['cached_repo']))
                            if repo.id==row['repo_id']:
                                self.search.save_candidates(scope,(ObservedRepository(repo,'1970-01-01T00:00:00+00:00'),),())
                        else:unresolved.append(DiscoveryCandidate(row['full_name'],row['repo_id'],tuple(json.loads(row['source_names'])),row['discovered_at']))
                    yield from self._collect_metadata_steps(unresolved,scope,budget,cancel_event,on_progress)
                    after=rows[-1]['catalog_key'];yield None
        finally:
            if hasattr(self.client,'budget'):
                self.client.budget=prior['budget'];self.client.clock=prior['clock']
        self.notes=list(dict.fromkeys(self.notes))[:30]
