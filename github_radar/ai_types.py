"""Values shared by AI analysis, recommendation logic and persistence."""

from dataclasses import dataclass

from .models import Repository


@dataclass(frozen=True, slots=True)
class AIRepositoryInput:
    repo: Repository
    readme_excerpt: str | None
    source_limited: bool
    candidate_rank: int | None = None
    observed_at: str | None = None


@dataclass(frozen=True, slots=True)
class RelevanceVerdict:
    repo_id: int
    verdict: str
    reason: str


@dataclass(frozen=True, slots=True)
class InsightText:
    summary: str
    purpose: str
    scenarios: str
    users: str
    highlights: tuple[str, ...]
    problem: str | None = None
    prerequisites: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectKind:
    primary: str
    secondary: tuple[str,...]
    zh: str
    en: str
    evidence: tuple[str,...]
    uncertain: bool


@dataclass(frozen=True, slots=True)
class ProjectExplanation:
    zh: InsightText
    en: InsightText
    relevance: str | None
    evidence: tuple[str, ...]
    source_limited: bool
    schema_version: int = 1
    readme_hash: str | None = None
    project_kind: ProjectKind | None = None


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    inputs: tuple[AIRepositoryInput, ...]
    next_page: int
    next_offset: int


@dataclass(frozen=True, slots=True)
class AIProgress:
    next_page: int
    next_offset: int
    checked_count: int
