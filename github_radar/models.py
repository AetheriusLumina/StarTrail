from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Repository:
    id: int
    full_name: str
    html_url: str
    description: str
    topics: tuple[str, ...]
    language: str | None
    stars: int
    archived: bool


@dataclass(frozen=True, slots=True)
class KeywordRule:
    id: int
    term: str
    min_stars: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class StarSnapshot:
    repo_id: int
    local_date: str
    stars: int
    observed_at: str


@dataclass(frozen=True, slots=True)
class StarDay:
    stat_date: str
    added: int


@dataclass(frozen=True, slots=True)
class OfficialStarWeek:
    week: int
    days: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Recommendation:
    repo_id: int
    local_date: str
    section: str
    keyword_id: int | None
    star_delta: int | None
    baseline_at: str | None
    observed_at: str
    metric_basis: str | None = None
    metric_date: str | None = None
    rank: int | None = None
    display_role: str | None = None
    matched_keyword_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class GrowthCoverage:
    candidate_count: int
    scored_count: int
    source_names: tuple[str, ...]
    stat_date: str | None
    metric_basis: str
    catalog_count: int | None = None
    failed_count: int = 0
    stop_reasons: tuple[str, ...] = ()
