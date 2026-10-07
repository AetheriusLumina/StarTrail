"""Durable search frontiers. A collected candidate is not a displayed item."""
import json
from contextlib import closing, nullcontext
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
        "CREATE INDEX IF NOT EXISTS idx_search_candidate_name ON search_candidates(scope_key,json_extract(payload,'$.full_name') COLLATE NOCASE)",
        'CREATE TABLE IF NOT EXISTS search_availability(repo_id INTEGER PRIMARY KEY,checked_date TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS search_http_cache(path TEXT PRIMARY KEY,run_id TEXT NOT NULL,payload TEXT NOT NULL)',
        "CREATE TABLE IF NOT EXISTS search_matches(scope_key TEXT NOT NULL,repo_id INTEGER NOT NULL,term TEXT NOT NULL,kind TEXT NOT NULL,content_hash TEXT NOT NULL,observed_at TEXT NOT NULL,PRIMARY KEY(scope_key,repo_id,term,kind))",
        "CREATE TABLE IF NOT EXISTS search_star_days(repo_id INTEGER NOT NULL,stat_date TEXT NOT NULL,rule TEXT NOT NULL,added INTEGER NOT NULL,fetched_at TEXT NOT NULL,PRIMARY KEY(repo_id,stat_date,rule))",
    ): db.execute(statement)

def scope_key(scope):
    # Day and Star observations deliberately do not invalidate semantic judgments.
    return json.dumps([scope.section,scope.keyword_id,scope.term,scope.min_stars,
                       scope.model_id,scope.rules_version,scope.expansion_hash],ensure_ascii=False,separators=(',',':'))

def _repository(data):
    return Repository(**{**data,'topics':tuple(data['topics'])})

class SearchStore:
    def __init__(self, store): self.store=store

    def mark_available(self,repo_id):
        with closing(self.store._connect()) as db,db:db.execute('DELETE FROM search_availability WHERE repo_id=?',(repo_id,))

    def mark_unavailable(self,repo_id,day):
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT OR REPLACE INTO search_availability VALUES(?,?)',(repo_id,day))

    def unavailable(self,repo_id,day):
        with closing(self.store._connect()) as db:
            return db.execute('SELECT 1 FROM search_availability WHERE repo_id=? AND checked_date=?',(repo_id,day)).fetchone() is not None

    def import_candidates(self,scope):
        # A model/threshold change must not throw away previously discovered IDs.
        after=0
        while True:
            with closing(self.store._connect()) as db:
                rows=db.execute("SELECT repo_id,payload,observed_at FROM (SELECT repo_id,payload,observed_at,ROW_NUMBER() OVER(PARTITION BY repo_id ORDER BY julianday(observed_at) DESC,scope_key DESC) AS position FROM search_candidates WHERE scope_key!=? AND json_extract(scope_key,'$[0]')=? AND json_extract(scope_key,'$[1]') IS ? AND json_extract(scope_key,'$[2]')=? AND repo_id>?) WHERE position=1 ORDER BY repo_id LIMIT 100",
                    (scope_key(scope),scope.section,scope.keyword_id,scope.term,after)).fetchall()
            if not rows:return
            self.save_candidates(scope,tuple(ObservedRepository(_repository(json.loads(r['payload'])),r['observed_at']) for r in rows),())
            after=rows[-1]['repo_id']

    @staticmethod
    def content_hash(repo):
        from hashlib import sha256
        return sha256(json.dumps([repo.id,repo.full_name,repo.description,sorted(repo.topics),repo.language],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

    def save_match(self,scope,repo,term,kind,observed_at):
        if kind not in ('github_query','ai_search','github_name_topic'):raise ValueError('检索依据类型无效')
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT OR REPLACE INTO search_matches VALUES(?,?,?,?,?,?)',
                (scope_key(scope),repo.id,term,kind,self.content_hash(repo),observed_at))

    def matching_verdict(self,scope,observation,terms,*,strict=False):
        import re,unicodedata
        repo=observation.repo
        with closing(self.store._connect()) as db:
            matches=db.execute('SELECT * FROM search_matches WHERE scope_key=? AND repo_id=?',(scope_key(scope),repo.id)).fetchall()
        for match in matches:
            if strict and match['kind']!='github_name_topic':continue
            # A fetched query match may live in README; do not pretend that an
            # unrelated old purpose still matches after metadata changes.
            age=datetime.fromisoformat(observation.observed_at)-datetime.fromisoformat(match['observed_at'])
            if match['content_hash']==self.content_hash(repo) and timedelta(0)<=age<timedelta(days=1):
                prefix='AI 搜索依据：' if match['kind']=='ai_search' else '检索匹配：'
                return RelevanceVerdict(repo.id,'relevant',prefix+match['term'])
        parts=(repo.full_name.split('/')[-1],*repo.topics) if strict else (repo.full_name,repo.description,*repo.topics)
        text=unicodedata.normalize('NFKC',' '.join(parts)).casefold()
        for term in terms:
            words=re.findall(r'[\w]+',unicodedata.normalize('NFKC',term).casefold())
            if strict:
                pattern=r'(?<![a-z0-9])'+r'[\s_-]+'.join(re.escape(word) for word in words)+r'(?![a-z0-9])'
                if words and any(re.search(pattern,unicodedata.normalize('NFKC',part).casefold()) for part in parts):
                    return RelevanceVerdict(repo.id,'relevant','名称或主题匹配：'+term)
                continue
            if words and all((word in text if any('\u3400'<=c<='\u9fff' for c in word) else re.search(r'(?<![a-z0-9])'+re.escape(word)+r'(?![a-z0-9])',text)) for word in words):
                return RelevanceVerdict(repo.id,'relevant','检索匹配：'+term)
        if strict:
            allowed={re.sub(r'[-_\s]+',' ',unicodedata.normalize('NFKC',term).casefold()).strip() for term in terms}
            for evidence in self.store.source_evidence(repo.id):
                age=datetime.fromisoformat(observation.observed_at)-datetime.fromisoformat(evidence.observed_at)
                if evidence.source_name!='trendshift_topic' or not timedelta(0)<=age<timedelta(days=1):continue
                for topic in evidence.topics:
                    if re.sub(r'[-_\s]+',' ',unicodedata.normalize('NFKC',topic).casefold()).strip() in allowed:
                        return RelevanceVerdict(repo.id,'relevant','网站主题匹配：'+topic)
        return RelevanceVerdict(repo.id,'uncertain','没有当前关键词的可靠检索依据；未进行AI审核')

    def save_star_day(self,repo_id,day,now):
        from .models import StarDay
        if type(repo_id) is not int or repo_id<=0 or type(day.added) is not int or day.added<0 or now.tzinfo is None:
            raise ValueError('官方日增证据无效')
        if datetime.fromisoformat(day.stat_date).date()>=now.astimezone(timezone.utc).date():raise ValueError('官方统计日未结束')
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT OR REPLACE INTO search_star_days VALUES(?,?,?,?,?)',(repo_id,day.stat_date,'utc-sunday-v2',day.added,now.isoformat()))
            # Identity/catalog/history stay durable; obsolete numeric cache is bounded.
            db.execute('DELETE FROM search_star_days WHERE stat_date<?',((now.astimezone(timezone.utc).date()-timedelta(days=30)).isoformat(),))

    def load_star_day(self,repo_id,stat_date,now):
        from .models import StarDay
        with closing(self.store._connect()) as db:
            row=db.execute("SELECT added,fetched_at FROM search_star_days WHERE repo_id=? AND stat_date=? AND rule='utc-sunday-v2'",(repo_id,stat_date)).fetchone()
        if row:
            age=now-datetime.fromisoformat(row['fetched_at'])
            if timedelta(0)<=age<timedelta(hours=6):return StarDay(stat_date,row['added'])
        return None

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

    def observations_today(self,scope):
        from .search_types import ObservedRepository
        after=None
        while page:=self.candidate_page(scope,after,100):
            for item in page:
                if datetime.fromisoformat(item.observed_at).astimezone().date().isoformat()==scope.local_date:yield item
            after=page[-1].repo.id

    def observation_named_today(self,scope,name):
        with closing(self.store._connect()) as db:
            row=db.execute("SELECT payload,observed_at FROM search_candidates WHERE scope_key=? AND json_extract(payload,'$.full_name')=? COLLATE NOCASE",(scope_key(scope),name)).fetchone()
        if row and datetime.fromisoformat(row['observed_at']).astimezone().date().isoformat()==scope.local_date:
            return ObservedRepository(_repository(json.loads(row['payload'])),row['observed_at'])
        return None

    def judgment_ids(self,scope,kind):
        with closing(self.store._connect()) as db:
            return {r[0] for r in db.execute('SELECT DISTINCT repo_id FROM search_judgments WHERE scope_key=? AND kind=?',(scope_key(scope),kind))}

    def candidate_count(self,scope):
        with closing(self.store._connect()) as db:
            return db.execute('SELECT COUNT(*) FROM search_candidates WHERE scope_key=?',(scope_key(scope),)).fetchone()[0]

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
                for field in ('newly_checked','expansion_calls','search_calls','judgment_calls','catalog_calls','checked_completed'):
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

    def publish(self,value,lease,*,now=None,cancel_event=None,_connection=None):
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
        with (closing(self.store._connect()) if _connection is None else nullcontext(_connection)) as db, (db if _connection is None else nullcontext()):
            if _connection is None:db.execute('BEGIN IMMEDIATE')
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
            snapshots=[];accepted=[]
            for observation in value.observations:
                newest=db.execute('SELECT observed_at FROM snapshots WHERE repo_id=? ORDER BY julianday(observed_at) DESC LIMIT 1',(observation.repo.id,)).fetchone()
                if not newest or datetime.fromisoformat(newest[0])<=datetime.fromisoformat(observation.observed_at):accepted.append(observation.repo)
            for item in value.snapshots:
                old=db.execute('SELECT observed_at FROM snapshots WHERE repo_id=? AND local_date=?',(item.repo_id,item.local_date)).fetchone()
                if not old or datetime.fromisoformat(old[0])<=datetime.fromisoformat(item.observed_at):snapshots.append(item)
            if cancel_event and cancel_event.is_set():raise ValueError('检索已取消，结果未发布')
            self.store.commit_daily(scope.local_date,accepted,snapshots,recommendations,
                completed_at=now.isoformat(),completed_sections=[(scope.section,scope.keyword_id)],growth_coverage=value.coverage,_connection=db)

    def publish_all(self,values,leases,*,now=None,cancel_event=None,refresh_lease=None):
        if len(values)!=len(leases) or not values:raise ValueError('榜单整份提交范围无效')
        now=now or datetime.now().astimezone()
        sections={(v.scope.section,v.scope.keyword_id) for v in values}
        if len(sections)!=len(values):raise ValueError('重复榜单范围')
        with closing(self.store._connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if refresh_lease:
                row=db.execute('SELECT generation,owner,expires_at FROM search_runs WHERE job_id=?',(refresh_lease.job_id,)).fetchone()
                if not row or row['generation']!=refresh_lease.generation or row['owner']!=refresh_lease.owner or row['expires_at']<=now.timestamp():raise ValueError('整份更新任务已失效')
                enabled={r[0] for r in db.execute('SELECT id FROM keywords WHERE enabled=1 AND deleted_at IS NULL')}
                if sections!={('growth',None),*(('keyword',i) for i in enabled)}:raise ValueError('关键词列表已变化，整份榜单未保存')
            # Remove target sections only inside this transaction. A second
            # module's validation/disk error restores every old row and ledger.
            for value in values:
                db.execute('DELETE FROM recommendations WHERE local_date=? AND section=? AND COALESCE(keyword_id,0)=?',(value.scope.local_date,value.scope.section,value.scope.keyword_id or 0))
            for value,lease in zip(values,leases):self.publish(value,lease,now=now,cancel_event=cancel_event,_connection=db)

    def frontier(self,scope):
        # Close each read before network/cache writes; no cursor spans a task.
        after_stars=None;after_id=0;visited=set()
        while True:
            with closing(self.store._connect()) as db:
                rows=db.execute("SELECT repo_id,payload,observed_at,json_extract(payload,'$.stars') AS stars FROM search_candidates WHERE scope_key=? AND (? IS NULL OR json_extract(payload,'$.stars')<? OR (json_extract(payload,'$.stars')=? AND repo_id>?)) ORDER BY stars DESC,repo_id LIMIT 100",
                    (scope_key(scope),after_stars,after_stars,after_stars,after_id)).fetchall()
            if not rows:return
            for row in rows:
                if row['repo_id'] not in visited and not self.unavailable(row['repo_id'],scope.local_date):
                    visited.add(row['repo_id'])
                    yield row
            after_stars=rows[-1]['stars'];after_id=rows[-1]['repo_id']


    def refresh_timing(self):
        with closing(self.store._connect()) as db:
            row=db.execute("SELECT value FROM settings WHERE name='search_refresh_timing'").fetchone()
        try:return json.loads(row[0]) if row else None
        except (ValueError,TypeError):return None

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

class SearchRequestCache:
    """Same-refresh public GET facts on disk, never credentials or error bodies."""
    def __init__(self,store):
        self.store=store;self.run_id=uuid.uuid4().hex
        with closing(store._connect()) as db,db:
            db.execute('DELETE FROM search_http_cache WHERE run_id NOT IN (SELECT run_id FROM search_http_cache GROUP BY run_id ORDER BY MAX(rowid) DESC LIMIT 2)')
    def get(self,path):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM search_http_cache WHERE path=? AND run_id=?',(path,self.run_id)).fetchone()
        return json.loads(row[0]) if row else None
    def put(self,path,payload):
        text=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(text.encode('utf-8'))>262144:return
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT OR REPLACE INTO search_http_cache VALUES(?,?,?)',(path,self.run_id,text))
