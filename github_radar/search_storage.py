"""Durable search frontiers. A collected candidate is not a displayed item."""
import json
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone, timedelta
import uuid
from .models import Repository
from .ai_types import RelevanceVerdict
from .discovery_types import DiscoveryBatch
from .search_types import QueryExpansion, ObservedRepository, GrowthAssessment, SearchProgress

def initialize_search_schema(db):
    # Individual statements keep migration inside RadarStore's existing transaction.
    for statement in (
        'CREATE TABLE IF NOT EXISTS query_expansions (scope_key TEXT PRIMARY KEY,payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS search_readmes(repo_id INTEGER PRIMARY KEY,fetched_at TEXT NOT NULL,excerpt TEXT,limited INTEGER NOT NULL)',
        """CREATE TABLE IF NOT EXISTS search_candidates (scope_key TEXT NOT NULL,
            repo_id INTEGER NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(scope_key,repo_id))""",
        """CREATE TABLE IF NOT EXISTS search_judgments (scope_key TEXT NOT NULL,
            repo_id INTEGER NOT NULL, fingerprint TEXT NOT NULL, kind TEXT NOT NULL,
            checked_at TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(scope_key,repo_id,fingerprint,kind))""",
        """CREATE TABLE IF NOT EXISTS search_runs (job_id TEXT PRIMARY KEY,
            scope_key TEXT, generation INTEGER NOT NULL DEFAULT 0,
            owner TEXT, expires_at REAL NOT NULL DEFAULT 0, payload TEXT NOT NULL)""",
        'CREATE TABLE IF NOT EXISTS search_cursors (scope_key TEXT NOT NULL,source TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(scope_key,source))',
        'CREATE INDEX IF NOT EXISTS idx_search_candidate_page ON search_candidates(scope_key,repo_id)',
    ): db.execute(statement)

def scope_key(scope):
    # Day and Star observations deliberately do not invalidate semantic judgments.
    return json.dumps([scope.section,scope.keyword_id,scope.term,scope.min_stars,
                       scope.model_id,scope.rules_version,scope.expansion_hash],ensure_ascii=False,separators=(',',':'))

def _repository(data):
    return Repository(**{**data,'topics':tuple(data['topics'])})

class SearchStore:
    def __init__(self, store): self.store=store

    def load_expansion(self,scope):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM query_expansions WHERE scope_key=?',(scope_key(scope),)).fetchone()
        if row is None:return None
        data=json.loads(row[0])
        return QueryExpansion(data['original'],tuple(data['terms']),tuple(data['topics']),data['version'])

    def save_expansion(self,scope,value):
        if value.original!=scope.term or len(value.terms)>6 or any(not isinstance(t,str) or not 1<=len(t)<=120 for t in value.terms):
            raise ValueError('关键词扩展无效')
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT INTO query_expansions VALUES(?,?) ON CONFLICT(scope_key) DO UPDATE SET payload=excluded.payload',
                       (scope_key(scope),json.dumps(asdict(value),ensure_ascii=False)))

    def save_candidates(self,scope,observations,evidence):
        key=scope_key(scope)
        with closing(self.store._connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            for observed in observations:
                if type(observed.repo.id) is not int or observed.repo.id<1:raise ValueError('仓库ID无效')
                # ISO strings can use different offsets; compare instants in Python.
                row=db.execute('SELECT observed_at FROM search_candidates WHERE scope_key=? AND repo_id=?',(key,observed.repo.id)).fetchone()
                if row and datetime.fromisoformat(row[0])>datetime.fromisoformat(observed.observed_at):continue
                db.execute('INSERT INTO search_candidates VALUES(?,?,?,?) ON CONFLICT(scope_key,repo_id) DO UPDATE SET observed_at=excluded.observed_at,payload=excluded.payload',
                    (key,observed.repo.id,observed.observed_at,json.dumps(asdict(observed.repo),ensure_ascii=False)))
        if evidence:
            from .discovery_types import DiscoveryCandidate
            candidates=tuple(DiscoveryCandidate(e.full_name,e.repo_id,(e.source_name,),e.observed_at,evidence=(e,)) for e in evidence)
            self.save_unresolved(scope,candidates)

    def save_unresolved(self,scope,candidates):
        self.store.save_discovery_batch(DiscoveryBatch('ai_web_search',tuple(candidates),None,True,()))

    def candidate_page(self,scope,after_id,limit=100):
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('候选分页大小无效')
        with closing(self.store._connect()) as db:
            rows=db.execute('SELECT payload,observed_at FROM search_candidates WHERE scope_key=? AND repo_id>? ORDER BY repo_id LIMIT ?',
                            (scope_key(scope),after_id or 0,limit)).fetchall()
        return tuple(ObservedRepository(_repository(json.loads(r[0])),r[1]) for r in rows)

    def load_judgment(self,scope,repo_id,fingerprint,kind):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM search_judgments WHERE scope_key=? AND repo_id=? AND fingerprint=? AND kind=?',
                           (scope_key(scope),repo_id,fingerprint,kind)).fetchone()
        if row is None:return None
        return (RelevanceVerdict if kind=='semantic' else GrowthAssessment)(**json.loads(row[0]))

    def save_judgments(self,scope,prepared,verdicts,assessments,checked_at):
        by_id={item.observation.repo.id:item for item in prepared}
        with closing(self.store._connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            for kind,items,field in (('semantic',verdicts,'semantic_fingerprint'),('evidence',assessments,'evidence_fingerprint')):
                for item in items:
                    if item.repo_id not in by_id:raise ValueError('判断不属于当前候选')
                    allowed=('relevant','irrelevant','uncertain') if kind=='semantic' else ('supported','conflict','uncertain')
                    status=item.verdict if kind=='semantic' else item.status
                    if status not in allowed or not item.reason.strip():raise ValueError('判断无效')
                    db.execute('INSERT INTO search_judgments VALUES(?,?,?,?,?,?) ON CONFLICT(scope_key,repo_id,fingerprint,kind) DO UPDATE SET checked_at=excluded.checked_at,payload=excluded.payload',
                        (scope_key(scope),item.repo_id,getattr(by_id[item.repo_id],field),kind,checked_at,json.dumps(asdict(item),ensure_ascii=False)))

    def save_progress(self,progress,*,lease=None):
        with closing(self.store._connect()) as db,db:
            payload=json.dumps(asdict(progress),ensure_ascii=False)
            if lease is not None:
                # The same job ID survives ownership changes; stale exceptions
                # must never overwrite successor counters or terminal state.
                return db.execute('UPDATE search_runs SET payload=? WHERE job_id=? AND generation=? AND owner=?',
                    (payload,progress.job_id,lease.generation,lease.owner)).rowcount==1
            db.execute('INSERT INTO search_runs(job_id,payload) VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload',
                       (progress.job_id,payload))
            return True

    def progress(self,job_id):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM search_runs WHERE job_id=?',(job_id,)).fetchone()
        if row is None:return None
        data=json.loads(row[0]);data['notes']=tuple(data['notes'])
        return SearchProgress(**data)


    def cursor(self,scope,source):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM search_cursors WHERE scope_key=? AND source=?',(scope_key(scope),source)).fetchone()
        return json.loads(row[0]) if row else None

    def save_cursor(self,scope,source,value):
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT INTO search_cursors VALUES(?,?,?) ON CONFLICT(scope_key,source) DO UPDATE SET payload=excluded.payload',
                (scope_key(scope),source,json.dumps(value,ensure_ascii=False)))

    def claim_run(self,scope,owner,*,now=None,ttl=1800,namespace="module"):
        now=datetime.now().timestamp() if now is None else now
        key=namespace+':'+json.dumps([scope.section,scope.keyword_id,scope.local_date,scope.stat_date],separators=(',',':'))
        with closing(self.store._connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM search_runs WHERE scope_key=? ORDER BY generation DESC LIMIT 1',(key,)).fetchone()
            if row and row['owner'] and row['expires_at']>now:
                return SearchLease(row['job_id'],row['generation'],row['owner'],False)
            job_id=row['job_id'] if row else uuid.uuid4().hex
            generation=(row['generation'] if row else 0)+1
            progress=SearchProgress(job_id,scope.section,scope.keyword_id,scope.local_date)
            if row:
                previous=json.loads(row['payload'])
                for field in ('newly_checked','expansion_calls','search_calls','judgment_calls'):
                    progress=replace(progress,**{field:previous.get(field,0)})
            db.execute('INSERT INTO search_runs VALUES(?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET generation=excluded.generation,owner=excluded.owner,expires_at=excluded.expires_at,payload=excluded.payload',
                (job_id,key,generation,owner,now+ttl,json.dumps(asdict(progress),ensure_ascii=False)))
            return SearchLease(job_id,generation,owner,True)

    def renew_run(self,lease,*,now=None,ttl=1800):
        now=datetime.now().timestamp() if now is None else now
        with closing(self.store._connect()) as db,db:
            count=db.execute('UPDATE search_runs SET expires_at=? WHERE job_id=? AND generation=? AND owner=? AND expires_at>?',
                (now+ttl,lease.job_id,lease.generation,lease.owner,now)).rowcount
        return count==1

    def finish_run(self,lease):
        with closing(self.store._connect()) as db,db:
            db.execute('UPDATE search_runs SET owner=NULL,expires_at=0 WHERE job_id=? AND generation=? AND owner=?',
                (lease.job_id,lease.generation,lease.owner))

    def publish(self,value,lease,*,now=None,cancel_event=None):
        now=now or datetime.now().astimezone();scope=value.scope
        if scope.local_date!=now.date().isoformat():raise ValueError('日期已变化，旧任务结果不再发布')
        if scope.section=='growth' and scope.stat_date!=(now.astimezone(timezone.utc).date()-timedelta(days=1)).isoformat():
            raise ValueError('官方统计日已变化')
        if not lease.acquired or value.generation!=lease.generation:raise ValueError('任务发布权限已失效')
        by_id={o.repo.id:o for o in value.observations}
        for snapshot in value.snapshots:
            observed=by_id.get(snapshot.repo_id)
            if not observed or snapshot.local_date!=scope.local_date or snapshot.observed_at!=observed.observed_at or snapshot.stars!=observed.repo.stars:
                raise ValueError('快照与真实观测不一致')
            at=datetime.fromisoformat(snapshot.observed_at)
            if at.tzinfo is None or at.astimezone(now.tzinfo).date()!=now.date():raise ValueError('缓存不能伪装为今日快照')
        for item in value.recommendations:
            if item.repo_id not in by_id or item.section!=scope.section or item.keyword_id!=scope.keyword_id or item.local_date!=scope.local_date:
                raise ValueError('推荐不属于当前检索范围')
            if scope.section=='growth' and (item.metric_date!=scope.stat_date or item.metric_basis!='github_daily_new' or not item.star_delta or item.star_delta<0):
                raise ValueError('增长推荐缺少正确统计日的官方数字')
        with closing(self.store._connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if cancel_event and cancel_event.is_set():raise ValueError('检索已取消，结果未发布')
            row=db.execute('SELECT generation,owner,expires_at FROM search_runs WHERE job_id=?',(lease.job_id,)).fetchone()
            if not row or row['generation']!=lease.generation or row['owner']!=lease.owner or row['expires_at']<=now.timestamp():
                raise ValueError('任务已被取消或替代，保留当前数据')
            selected=db.execute("SELECT value FROM settings WHERE name='ai_model'").fetchone()
            if selected and selected[0] and selected[0]!=scope.model_id:
                raise ValueError('模型已变化，旧任务不再发布')
            if scope.section=='keyword':
                if scope.expansion_hash:
                    from hashlib import sha256
                    base=replace(scope,expansion_hash='')
                    expansion=db.execute('SELECT payload FROM query_expansions WHERE scope_key=?',(scope_key(base),)).fetchone()
                    digest=sha256(json.dumps(json.loads(expansion[0]),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() if expansion else None
                    if digest!=scope.expansion_hash:raise ValueError('扩词已变化，旧任务不再发布')
                rule=db.execute('SELECT * FROM keywords WHERE id=?',(scope.keyword_id,)).fetchone()
                if not rule or not rule['enabled'] or rule['deleted_at'] is not None or rule['term']!=scope.term or rule['min_stars']!=scope.min_stars:
                    raise ValueError('关键词已变化，旧任务不再发布')
            # A concurrent module may have claimed a repository since ranking.
            occupied={r[0] for r in db.execute('SELECT repo_id FROM recommendations WHERE local_date=? AND NOT(section=? AND COALESCE(keyword_id,0)=?)',
                (scope.local_date,scope.section,scope.keyword_id or 0))}
            recommendations=[r for r in value.recommendations if r.repo_id not in occupied]
            db.execute('DELETE FROM recommendations WHERE local_date=? AND section=? AND COALESCE(keyword_id,0)=?',
                (scope.local_date,scope.section,scope.keyword_id or 0))
            snapshots=[]
            for item in value.snapshots:
                old=db.execute('SELECT observed_at FROM snapshots WHERE repo_id=? AND local_date=?',(item.repo_id,item.local_date)).fetchone()
                if not old or datetime.fromisoformat(old[0])<=datetime.fromisoformat(item.observed_at):snapshots.append(item)
            if cancel_event and cancel_event.is_set():raise ValueError('检索已取消，结果未发布')
            self.store.commit_daily(scope.local_date,[o.repo for o in value.observations],snapshots,recommendations,
                completed_at=now.isoformat(),completed_sections=[(scope.section,scope.keyword_id)],growth_coverage=value.coverage,_connection=db)

    def frontier(self,scope):
        # Close each read before network/cache writes; no cursor spans a task.
        after_stars=None;after_id=0;visited=set()
        while True:
            with closing(self.store._connect()) as db:
                rows=db.execute("SELECT repo_id,payload,observed_at,json_extract(payload,'$.stars') AS stars FROM search_candidates WHERE scope_key=? AND (? IS NULL OR json_extract(payload,'$.stars')<? OR (json_extract(payload,'$.stars')=? AND repo_id>?)) ORDER BY stars DESC,repo_id LIMIT 100",
                    (scope_key(scope),after_stars,after_stars,after_stars,after_id)).fetchall()
            if not rows:return
            for row in rows:
                if row['repo_id'] not in visited:
                    visited.add(row['repo_id'])
                    yield row
            after_stars=rows[-1]['stars'];after_id=rows[-1]['repo_id']


    def current_progress(self,day):
        with closing(self.store._connect()) as db:
            rows=db.execute("SELECT payload FROM search_runs WHERE scope_key LIKE 'module:%' AND json_extract(payload,'$.local_date')=?",(day,)).fetchall()
        values=[]
        for row in rows:
            data=json.loads(row[0])
            if data.get('local_date')==day:
                data['notes']=tuple(data.get('notes',()));values.append(SearchProgress(**data))
        return values

    def keyword_verdicts(self,keyword_id,model_id,repo_ids):
        # Only expose judgments for the currently displayed repository content.
        result={}
        with closing(self.store._connect()) as db:
            rows=db.execute("SELECT scope_key,repo_id,payload FROM search_judgments WHERE kind='semantic' ORDER BY checked_at DESC").fetchall()
        for row in rows:
            key=json.loads(row['scope_key'])
            if key[0]=='keyword' and key[1]==keyword_id and key[4]==model_id and row['repo_id'] in repo_ids:
                result.setdefault(row['repo_id'],RelevanceVerdict(**json.loads(row['payload'])))
        return result


@dataclass(frozen=True,slots=True)
class SearchLease:
    job_id:str
    generation:int
    owner:str
    acquired:bool
