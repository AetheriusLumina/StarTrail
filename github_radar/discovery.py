"""Persistent, budgeted discovery; downloading candidates never marks them seen."""
import json
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from .discovery_types import DiscoveryBatch, DiscoveryCandidate, DiscoveryReport, valid_repository_name
from .free_events import FreeEventsClient
from .github_client import GitHubRequestError
from .public_http import SourceRequestError
from .trendshift import TrendshiftClient


class DiscoveryCoordinator:
    def __init__(self, client, store, free_events=None, trendshift=None, clock=time.monotonic):
        self.client, self.store, self.clock = client, store, clock
        self.free_events = FreeEventsClient() if free_events is None else free_events
        self.trendshift = TrendshiftClient() if trendshift is None else trendshift

    def discover(self, rules, local_date, budget) -> DiscoveryReport:
        day = date.fromisoformat(local_date)
        observed = datetime.now(timezone.utc).isoformat()
        batches, notes = [], []
        for adapter in (self.free_events, self.trendshift):
            if adapter:
                if hasattr(adapter, "budget"):
                    adapter.budget, adapter.clock = budget, self.clock
        def save(batch):
            safe = tuple(c for c in batch.candidates if valid_repository_name(c.full_name))
            batch = replace(batch, candidates=safe)
            self.store.save_discovery_batch(batch)
            batches.append(batch); notes.extend(batch.notes)
        if self.trendshift and budget.can_spend("external", 1, self.clock()):
            try:
                for batch in self.trendshift.daily(observed):
                    save(batch)
                for rule in rules:
                    if rule.enabled and budget.can_spend("external", 1, self.clock()):
                        save(self.trendshift.topic(rule.term, observed))
            except (SourceRequestError, OSError, ValueError) as exc:
                notes.append(f"Trendshift: {exc}")
        if self.free_events and budget.can_spend("external", 1, self.clock()):
            try:
                stat_day = datetime.now(timezone.utc).date() - timedelta(days=1)
                save(self.free_events.discover(stat_day.isoformat(),
                     self.store.load_discovery_cursor("public_activity")))
            except (SourceRequestError, OSError, ValueError) as exc:
                notes.append(f"public_activity: {exc}")
        if self.client is not None:
            if hasattr(self.client, "budget"):
                self.client.budget, self.client.clock = budget, self.clock
            self._search(rules, day, observed, budget, save, notes)
            if budget.can_spend("core", 1, self.clock()) and hasattr(self.client, "public_repository_page"):
                try:
                    cursor = self.store.load_discovery_cursor("github_public_catalog")
                    self._spend("core", budget)
                    save(self.client.public_repository_page(int(cursor) if cursor else None))
                    self._observe(budget)
                except (GitHubRequestError, ValueError) as exc:
                    notes.append(f"github_public_catalog: {exc}")
        merged = {}
        for batch in batches:
            for candidate in batch.candidates:
                key = "id:" + str(candidate.repo_id) if candidate.repo_id else "name:" + candidate.full_name.casefold()
                old = merged.get(key)
                merged[key] = candidate if old is None else replace(candidate,
                    source_names=tuple(sorted(set(old.source_names + candidate.source_names))),
                    evidence=old.evidence + candidate.evidence,
                    cached_repo=candidate.cached_repo or old.cached_repo)
        if self.clock() >= budget.deadline:
            notes.append("本轮候选发现时间预算已到，断点已保存，继续核算已发现项目")
        return DiscoveryReport(tuple(merged.values()), tuple(batches), tuple(notes))

    def _spend(self, resource, budget):
        if getattr(self.client, "budget", None) is not budget:
            budget.spend(resource, 1, self.clock())

    def _observe(self, budget):
        for resource in ("core", "search"):
            value = getattr(self.client, resource + "_remaining", None)
            if value is not None:
                setattr(budget, resource + "_remaining", value)

    def _search(self, rules, day, observed, budget, save, notes):
        cursor = self.store.load_discovery_cursor("github_partition_search")
        try:
            queue = json.loads(cursor) if cursor else None
        except ValueError:
            queue = None
        if not queue:
            queue = [{"query": "archived:false stars:>=100", "start": (day-timedelta(days=30)).isoformat(),
                      "end": day.isoformat(), "page": 1}]
            queue += [{"query": f"{rule.term} in:name,description,readme archived:false stars:>={rule.min_stars}",
                       "start": "2007-01-01", "end": day.isoformat(), "page": 1}
                      for rule in rules if rule.enabled]
        while queue and budget.can_spend("search", 1, self.clock()):
            item = queue[0]
            query = item['query'] + f" created:{item['start']}..{item['end']}"
            try:
                self._spend("search", budget)
                result = self.client.search_page(query, item['page'])
                self._observe(budget)
            except (GitHubRequestError, ValueError) as exc:
                notes.append(f"github_partition_search: {exc}")
                break
            start, end = date.fromisoformat(item['start']), date.fromisoformat(item['end'])
            candidates = tuple(DiscoveryCandidate(r.full_name, r.id, ("github_partition_search",),
                                                   observed, cached_repo=r) for r in result.items)
            if result.total_count > 1000 and start < end:
                middle = start + (end-start)//2
                queue.pop(0)
                queue[0:0] = [dict(item, end=middle.isoformat(), page=1),
                              dict(item, start=(middle+timedelta(days=1)).isoformat(), page=1)]
                save(DiscoveryBatch("github_partition_search", candidates, json.dumps(queue), False, ()))
                continue
            if result.incomplete_results:
                notes.append("GitHub 搜索分区标为不完整，保留真实候选并稍后重试")
                save(DiscoveryBatch("github_partition_search", candidates, json.dumps(queue), False, ()))
                break
            if result.total_count > 1000:
                notes.append("单日分区超过 GitHub 1000 项限制，范围尚未完成")
            if item['page'] * 100 < min(result.total_count, 1000) and len(result.items) == 100:
                queue[0] = dict(item, page=item['page']+1)
            else:
                queue.pop(0)
            save(DiscoveryBatch("github_partition_search", candidates, json.dumps(queue), not queue, ()))
        # Persist even a split queue or a stop before the first successful page.
        save(DiscoveryBatch("github_partition_search", (), json.dumps(queue), not queue, ()))
