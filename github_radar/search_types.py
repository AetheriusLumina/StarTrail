"""Search contracts independent of persistence, UI and network clients."""
from dataclasses import dataclass
from .models import Repository, StarDay, StarSnapshot, Recommendation, GrowthCoverage
from .ai_types import AIRepositoryInput
from .discovery_types import DiscoveryCandidate, SourceEvidence

SEARCH_RULES_VERSION = "1"

@dataclass(frozen=True, slots=True)
class SearchScope:
    section: str
    keyword_id: int | None
    local_date: str
    stat_date: str | None
    term: str
    min_stars: int
    model_id: str | None
    rules_version: str = SEARCH_RULES_VERSION

    def __post_init__(self):
        if self.section not in ('growth','keyword'):
            raise ValueError('检索模块无效')
        if self.section=='keyword' and (type(self.keyword_id) is not int or self.keyword_id<1):
            raise ValueError('关键词ID无效')
        if self.section=='growth' and self.keyword_id is not None:
            raise ValueError('增长任务不能指定关键词')

@dataclass(frozen=True, slots=True)
class QueryExpansion:
    original: str
    terms: tuple[str,...]
    topics: tuple[str,...]
    version: str

@dataclass(frozen=True, slots=True)
class AISearchResult:
    candidates: tuple[DiscoveryCandidate,...]
    coverage: tuple[str,...]
    notes: tuple[str,...]
    web_search_calls: int

@dataclass(frozen=True, slots=True)
class ObservedRepository:
    repo: Repository
    observed_at: str

@dataclass(frozen=True, slots=True)
class PreparedCandidate:
    observation: ObservedRepository
    source: AIRepositoryInput
    evidence: tuple[SourceEvidence,...]
    semantic_fingerprint: str
    evidence_fingerprint: str
    official_day: StarDay | None

@dataclass(frozen=True, slots=True)
class GrowthAssessment:
    repo_id: int
    status: str
    reason: str

@dataclass(frozen=True, slots=True)
class SearchProgress:
    job_id: str
    section: str
    keyword_id: int | None
    local_date: str
    status: str = 'queued'
    stage: str = 'queued'
    collected: int = 0
    unique: int = 0
    cache_hits: int = 0
    newly_checked: int = 0
    pending: int = 0
    expansion_calls: int = 0
    search_calls: int = 0
    judgment_calls: int = 0
    limited: bool = False
    notes: tuple[str,...] = ()

@dataclass(frozen=True, slots=True)
class Publication:
    scope: SearchScope
    generation: int
    observations: tuple[ObservedRepository,...]
    snapshots: tuple[StarSnapshot,...]
    recommendations: tuple[Recommendation,...]
    coverage: GrowthCoverage | None
