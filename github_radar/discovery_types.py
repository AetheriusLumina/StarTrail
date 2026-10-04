"""Candidate discovery and source evidence, separate from recommendations."""
from dataclasses import dataclass
import re

from .models import Repository


def valid_repository_name(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value
    )) and value.split("/")[1] not in (".", "..")


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    source_name: str
    full_name: str
    source_url: str
    observed_at: str
    period: str
    stat_date: str | None
    rank_kind: str
    source_rank: int | None
    total_stars_text: str | None
    daily_added_text: str | None
    topics: tuple[str, ...] = ()
    day_boundary: str | None = None
    repo_id: int | None = None
    evidence_text: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    full_name: str
    repo_id: int | None
    source_names: tuple[str, ...]
    discovered_at: str
    activity_hint: int | None = None
    cached_repo: Repository | None = None
    evidence: tuple[SourceEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class DiscoveryBatch:
    source_name: str
    candidates: tuple[DiscoveryCandidate, ...]
    cursor: str | None
    batch_finished: bool
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryReport:
    candidates: tuple[DiscoveryCandidate, ...]
    batches: tuple[DiscoveryBatch, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchPage:
    items: tuple[Repository, ...]
    total_count: int
    incomplete_results: bool


@dataclass(slots=True)
class RequestBudget:
    core_remaining: int | None
    search_remaining: int | None
    deadline: float
    core_reserve: int = 10
    _core_probe: bool = False
    _search_probe: bool = False

    def can_spend(self, resource: str, cost: int, now: float) -> bool:
        if isinstance(cost, bool) or cost < 1 or now >= self.deadline:
            return False
        if resource == "external":
            return True
        if resource not in ("core", "search"):
            raise ValueError("未知请求额度类型")
        remaining = getattr(self, resource + "_remaining")
        if remaining is None:
            return cost == 1 and not getattr(self, "_" + resource + "_probe")
        reserve = self.core_reserve if resource == "core" else 0
        return remaining - cost >= reserve

    def spend(self, resource: str, cost: int, now: float) -> None:
        if not self.can_spend(resource, cost, now):
            raise ValueError("请求预算或更新时间已到限制")
        if resource == "external":
            return
        field = resource + "_remaining"
        if getattr(self, field) is None:
            setattr(self, "_" + resource + "_probe", True)
        else:
            setattr(self, field, getattr(self, field) - cost)

    def observe(self, headers) -> None:
        headers = {str(k).lower(): str(v) for k, v in headers.items()}
        resource = headers.get("x-ratelimit-resource")
        value = headers.get("x-ratelimit-remaining", "")
        if resource in ("core", "search") and value.isdecimal():
            setattr(self, resource + "_remaining", int(value))
