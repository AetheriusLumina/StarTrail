"""Collect official metadata and public evidence with resumable, bounded queries."""
import json
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime,timedelta
from .github_client import GitHubRequestError
from .public_http import SourceRequestError
from .search_storage import SearchStore,scope_key
from .search_types import ObservedRepository
from .discovery_types import DiscoveryBatch,DiscoveryCandidate,SourceEvidence,valid_repository_name

def literal_term(term):
    # Quote user values, keeping operators and quotes out of the query grammar.
    return '"'+term.replace('\\','\\\\').replace('"','\\"')+'"'

class SearchSources:
    def __init__(self,client,store,trending,trendshift,clock,free_events=None):
        self.client,self.store,self.trending,self.trendshift,self.clock=client,store,trending,trendshift,clock
        self.free_events=free_events
        self.search=SearchStore(store);self.notes=[];self.limited=False;self.collected=0

    def _call(self,budget,resource,func,*args,**kwargs):
        if getattr(self.client,'budget',None) is not budget:budget.spend(resource,1,self.clock())
        return func(*args,**kwargs)

    def _save_cursor(self,scope,term,queue):
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT INTO search_cursors VALUES(?,?,?) ON CONFLICT(scope_key,source) DO UPDATE SET payload=excluded.payload',
                (scope_key(scope),'github:'+term,json.dumps({'date':scope.local_date,'queue':queue})))

    def _queue(self,scope,term,start,end):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM search_cursors WHERE scope_key=? AND source=?',
                (scope_key(scope),'github:'+term)).fetchone()
        saved=json.loads(row[0]) if row else None
        fresh=[{'start':start.isoformat(),'end':end.isoformat(),'low':scope.min_stars,'high':None,'page':1}]
        if not saved:return fresh
        # A new date reopens page one as well as unfinished ranges: new projects
        # and recently changed Stars are not hidden behind yesterday's cursor.
        return (fresh if saved['date']!=scope.local_date else [])+saved['queue'] or fresh

    def _search_range(self,scope,term,start,end,budget,cancel_event,on_progress):
        queue=self._queue(scope,term,start,end)
        while queue and not cancel_event.is_set() and budget.can_spend('search',1,self.clock()):
            item=queue[0];stars=f"stars:>={item['low']}" if item['high'] is None else f"stars:{item['low']}..{item['high']}"
            base=literal_term(term)+' in:name,description,readme' if term else ''
            query=f"{base} archived:false {stars} created:{item['start']}..{item['end']}"
            try:page=self._call(budget,'search',self.client.search_page,query,page=item['page'],per_page=100,sort='stars')
            except (GitHubRequestError,ValueError,OSError) as exc:
                self.notes.append(str(exc));self.limited=True;break
            observed=datetime.now().astimezone().isoformat()
            self.search.save_candidates(scope,tuple(ObservedRepository(r,observed) for r in page.items),())
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
            self._save_cursor(scope,term,queue)
        if queue:self.limited=True
        self._save_cursor(scope,term,queue)

    def resolve_candidate(self,candidate,budget):
        if not valid_repository_name(candidate.full_name):return None
        try:
            repo=self._call(budget,'core',self.client.get_repository,candidate.full_name)
            if candidate.repo_id is not None and candidate.repo_id!=repo.id:
                self.notes.append('候选仓库身份变化，未覆盖原ID');return None
            observed=datetime.now().astimezone().isoformat()
            evidence=tuple(replace(e,repo_id=repo.id,full_name=repo.full_name) for e in candidate.evidence)
            self.store.save_discovery_batch(DiscoveryBatch('verified',
                (replace(candidate,repo_id=repo.id,full_name=repo.full_name,cached_repo=repo,evidence=evidence),),None,True,()))
            return ObservedRepository(repo,observed)
        except (GitHubRequestError,ValueError,OSError) as exc:
            self.notes.append(str(exc));self.limited=True;return None

    def collect(self,scope,expansion,ai_result,budget,cancel_event,on_progress):
        self.notes=[];self.limited=False;self.collected=0
        prior={name:getattr(self.client,name,None) for name in ('budget','clock')}
        if hasattr(self.client,'budget'):self.client.budget=budget;self.client.clock=self.clock
        candidates=list(ai_result.candidates) if ai_result else []
        try:
            # Trend pages are evidence lanes, not a membership requirement.
            if self.trendshift and budget.can_spend('external',1,self.clock()):
                self.trendshift.budget=budget;self.trendshift.clock=self.clock
                try:
                    batches=list(self.trendshift.daily(datetime.now().astimezone().isoformat()))
                    for topic in (expansion.topics if expansion else ()):
                        if cancel_event.is_set() or not budget.can_spend('external',1,self.clock()):break
                        batches.append(self.trendshift.topic(topic,datetime.now().astimezone().isoformat()))
                    for batch in batches:
                        candidates.extend(batch.candidates);self.notes.extend(batch.notes)
                        if not batch.batch_finished:self.limited=True
                except (SourceRequestError,ValueError,OSError) as exc:self.notes.append(str(exc));self.limited=True
            if self.trending and budget.can_spend('external',1,self.clock()):
                try:candidates.extend(DiscoveryCandidate(n,None,('github_trending',),datetime.now().astimezone().isoformat()) for n in self.trending.repo_names())
                except Exception as exc:self.notes.append('GitHub Trending: '+str(exc));self.limited=True
            if scope.section=='growth':
                tracked=[r for r,_,_ in self.store.followed_repositories()]+self.store.latest_growth_repositories(scope.local_date,limit=200)
                candidates.extend(DiscoveryCandidate(r.full_name,r.id,('tracked',),datetime.now().astimezone().isoformat()) for r in tracked)
                candidates.extend(self.store.catalog_candidates(500))
                if self.free_events and budget.can_spend('external',1,self.clock()):
                    self.free_events.budget=budget;self.free_events.clock=self.clock
                    try:
                        batch=self.free_events.discover(scope.stat_date,self.store.load_discovery_cursor('public_activity'))
                        self.store.save_discovery_batch(batch);candidates.extend(batch.candidates)
                        self.notes.extend(batch.notes)
                        if not batch.batch_finished:self.limited=True
                    except (SourceRequestError,ValueError,OSError) as exc:
                        self.notes.append('public_activity: '+str(exc));self.limited=True
            resolved={}
            for candidate in candidates:
                if cancel_event.is_set():self.limited=True;break
                key=candidate.full_name.casefold()
                if key in resolved:
                    observation=resolved[key]
                    evidence=tuple(replace(e,repo_id=observation.repo.id,full_name=observation.repo.full_name) for e in candidate.evidence)
                    self.store.save_discovery_batch(DiscoveryBatch('verified',
                        (replace(candidate,repo_id=observation.repo.id,full_name=observation.repo.full_name,cached_repo=observation.repo,evidence=evidence),),None,True,()))
                    continue
                if not budget.can_spend('core',1,self.clock()):self.limited=True;break
                observation=self.resolve_candidate(candidate,budget)
                if observation:
                    resolved[key]=observation
                    self.search.save_candidates(scope,(observation,),())
                    self.collected+=1;on_progress('collecting',self.collected)
            terms=(scope.term,*(expansion.terms if expansion else ())) if scope.section=='keyword' else ('',)
            start=date(2007,1,1) if scope.section=='keyword' else date.fromisoformat(scope.local_date)-timedelta(days=30)
            for term in dict.fromkeys(terms):
                if cancel_event.is_set() or not budget.can_spend('search',1,self.clock()):self.limited=True;break
                self._search_range(scope,term,start,date.fromisoformat(scope.local_date),budget,cancel_event,on_progress)
        finally:
            if hasattr(self.client,'budget'):
                self.client.budget=prior['budget'];self.client.clock=prior['clock']
        self.notes=list(dict.fromkeys(self.notes))[:30]
