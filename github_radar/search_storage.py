"""Durable search frontiers. A collected candidate is not a displayed item."""
import json
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from .models import Repository
from .ai_types import RelevanceVerdict
from .discovery_types import DiscoveryBatch
from .search_types import QueryExpansion, ObservedRepository, GrowthAssessment, SearchProgress

def initialize_search_schema(db):
    # Individual statements keep migration inside RadarStore's existing transaction.
    for statement in (
        'CREATE TABLE IF NOT EXISTS query_expansions (scope_key TEXT PRIMARY KEY,payload TEXT NOT NULL)',
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
                       scope.model_id,scope.rules_version],ensure_ascii=False,separators=(',',':'))

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

    def save_progress(self,progress):
        with closing(self.store._connect()) as db,db:
            db.execute('INSERT INTO search_runs(job_id,payload) VALUES(?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload',
                       (progress.job_id,json.dumps(asdict(progress),ensure_ascii=False)))

    def progress(self,job_id):
        with closing(self.store._connect()) as db:
            row=db.execute('SELECT payload FROM search_runs WHERE job_id=?',(job_id,)).fetchone()
        if row is None:return None
        data=json.loads(row[0]);data['notes']=tuple(data['notes'])
        return SearchProgress(**data)
