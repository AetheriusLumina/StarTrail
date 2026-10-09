"""Candidate discovery and source evidence, separate from recommendations."""
from dataclasses import dataclass, field
from threading import RLock
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


class RequestBudgetExceeded(ValueError):
    """A request was refused before sending because the round budget ended."""


@dataclass(slots=True)
class RequestBudget:
    core_remaining: int | None
    search_remaining: int | None
    deadline: float
    core_reserve: int = 10
    quota_event: object = field(default=None, repr=False, compare=False)
    _core_probe: bool = False
    _search_probe: bool = False
    _lock: object = field(default_factory=RLock, repr=False, compare=False)

    def can_spend(self, resource: str, cost: int, now: float) -> bool:
        with self._lock:
            if self.quota_event is not None and self.quota_event.is_set():return False
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
        with self._lock:
            if not self.can_spend(resource, cost, now):
                raise RequestBudgetExceeded("请求预算或更新时间已到限制")
            if resource == "external":
                return
            field = resource + "_remaining"
            if getattr(self, field) is None:
                setattr(self, "_" + resource + "_probe", True)
            else:
                setattr(self, field, getattr(self, field) - cost)

    def observe(self, headers) -> None:
        with self._lock:
            headers = {str(k).lower(): str(v) for k, v in headers.items()}
            resource = headers.get("x-ratelimit-resource")
            value = headers.get("x-ratelimit-remaining", "")
            if resource in ("core", "search") and value.isdecimal():
                current = getattr(self, resource + "_remaining")
                setattr(self, resource + "_remaining", int(value) if current is None else min(current, int(value)))

    def reset_identity(self):
        with self._lock:
            self.core_remaining = self.search_remaining = None
            self._core_probe = self._search_probe = False
