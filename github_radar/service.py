"""Coordinates one daily refresh without exposing network rules to the UI."""

from dataclasses import dataclass, replace
import time
from datetime import date, datetime, timedelta, timezone
from itertools import zip_longest

from .github_client import GitHubClient, GitHubRateLimitError, GitHubRequestError
from .models import GrowthCoverage, Recommendation, Repository, StarDay, StarSnapshot
from .ranking import latest_complete_day, select_daily_growth, select_growth, select_keyword
from .storage import RadarStore
from .trending import TrendingClient, TrendingUnavailable
from .discovery import DiscoveryCoordinator
from .discovery_types import DiscoveryBatch, DiscoveryCandidate, RequestBudget
from .cross_check import compare_source
from .ranking import (GrowthPick, GrowthSlot, allocate_keyword_groups,
                      confirmed_star_days, select_growth_slots)


@dataclass(frozen=True, slots=True)
class RefreshResult:
    local_date: str
    recommendations: tuple[Recommendation, ...]
    repositories: dict[int, Repository]
    updated_at: str | None
    status: str
    stale: bool
    message: str
    notes: tuple[str, ...]
    growth_coverage: GrowthCoverage | None = None


class RadarService:
    PAGE_SIZE = 100
    MAX_SEARCH_PAGES = 10
    MAX_TRENDING_LOOKUPS = 12
    CORE_RESERVE = 10
    REFRESH_SECONDS = 300
    DISCOVERY_SECONDS = 90

    def __init__(self, client: GitHubClient, store: RadarStore, trending: TrendingClient | None = None,
                 discovery=None, clock=time.monotonic):
        self.client = client
        self.store = store
        self.trending = trending
        self.clock = clock
        self.discovery = discovery or (DiscoveryCoordinator(client, store, clock=clock)
                                       if isinstance(client, GitHubClient) else None)

    def load_latest(self, today: str) -> RefreshResult:
        """Return saved data for startup without making a network request."""
        saved_date = self.store.latest_successful_date()
        failure = self.store.latest_refresh_failure()
        if failure is not None:
            records = self.store.daily_recommendations(saved_date) if saved_date else []
            return self._result(saved_date or today, records, "error", True, failure[1], ())
        if saved_date is None:
            return self._result(today, [], "empty", False, "尚无保存的数据", ())
        return self._result(
            saved_date,
            self.store.daily_recommendations(saved_date),
            "cached", saved_date != today,
            "显示上次保存的结果" if saved_date != today else "显示已保存的今日结果",
            (),
        )

    def refresh(self, local_date: str, observed_at: str) -> RefreshResult:
        if self.discovery is not None:
            prior_budget = getattr(self.client,"budget",None)
            prior_clock = getattr(self.client,"clock",None)
            try:
                return self._refresh_discovered(local_date, observed_at)
            finally:
                if hasattr(self.client,"budget"):
                    self.client.budget = prior_budget
                    self.client.clock = prior_clock
        date_value = date.fromisoformat(local_date)
        cutoff = (date_value - timedelta(days=7)).isoformat()
        completed = self.store.daily_sections(local_date)
        existing = self.store.daily_recommendations(local_date)
        seen = self.store.seen_repo_ids()
        new: list[Recommendation] = []
        new_sections: list[tuple[str, int | None]] = []
        fetched: dict[int, Repository] = {}
        notes: list[str] = []
        growth_coverage: GrowthCoverage | None = None

        try:
            has_today_growth = ("growth", None) in completed
            pending_rules = [
                rule for rule in self.store.list_keywords()
                if rule.enabled and ("keyword", rule.id) not in completed
            ]
            first_keyword_pages: dict[int, list[Repository]] = {}
            for rule in pending_rules:
                first_keyword_pages[rule.id] = self._search(
                    f"{rule.term} stars:>={rule.min_stars}", page=1
                )
            growth_repos, sources = self._growth_candidates(
                cutoff, local_date, has_today_growth, notes,
                search_reserve=2 if pending_rules else 0,
            )
            for repo in growth_repos:
                fetched[repo.id] = repo
            saved_repos = self.store.repositories_for_ids([item.repo_id for item in existing])
            for item in existing:
                if item.repo_id in fetched:
                    continue
                saved = saved_repos[item.repo_id]
                refreshed = self.client.get_repository(saved.full_name)
                if refreshed.id != item.repo_id:
                    raise GitHubRequestError("GitHub 仓库身份已变化，请稍后再试")
                fetched[refreshed.id] = refreshed

            if not has_today_growth:
                eligible = [repo for repo in growth_repos if not repo.archived]
                today_utc = datetime.fromisoformat(observed_at).astimezone(timezone.utc).date().isoformat()
                daily: dict[int, StarDay] = {}
                for repo in eligible:
                    if not self._has_core_budget():
                        notes.append("GitHub 请求额度有限，部分候选尚未核算")
                        break
                    day = latest_complete_day(self.client.star_history(repo.full_name), today_utc)
                    if day is not None:
                        daily[repo.id] = day
                stat_date = max((day.stat_date for day in daily.values()), default=None)
                if stat_date is not None:
                    growth = select_daily_growth(eligible, daily, stat_date)
                    scored_count = sum(day.stat_date == stat_date for day in daily.values())
                    basis = "github_daily_new"
                else:
                    baselines = self.store.snapshots_before(local_date, [repo.id for repo in eligible])
                    growth = select_growth(eligible, baselines)
                    scored_count = 0
                    basis = "local_snapshot" if growth and growth[0].delta is not None else "pending"
                    if basis == "pending" and growth:
                        notes.append("首次采样：正在建立增长记录")
                if len(growth) < 5:
                    notes.append("增长区符合条件的项目不足 5 个")
                growth_coverage = GrowthCoverage(len(eligible), scored_count, tuple(sources), stat_date, basis)
                for pick in growth:
                    new.append(Recommendation(
                        pick.repo.id, local_date, "growth", None, pick.delta,
                        pick.baseline_at, observed_at, pick.metric_basis, pick.metric_date,
                    ))
                    seen.add(pick.repo.id)
                new_sections.append(("growth", None))

            for rule in pending_rules:
                picks: list[Repository] = []
                keyword_pool: list[Repository] = []
                page = 1
                while len(picks) < 5 and page <= self.MAX_SEARCH_PAGES:
                    page_items = first_keyword_pages[rule.id] if page == 1 else self._search(
                        f"{rule.term} stars:>={rule.min_stars}", page=page
                    )
                    for repo in page_items:
                        fetched[repo.id] = repo
                    keyword_pool.extend(page_items)
                    picks = select_keyword(keyword_pool, rule, seen)
                    if len(page_items) < self.PAGE_SIZE:
                        break
                    page += 1
                if len(picks) < 5:
                    notes.append(f"关键词「{rule.term}」符合条件的新项目不足 5 个")
                ranks = {r.id: index for index,r in enumerate(
                    select_keyword(keyword_pool,rule,set(),limit=len(keyword_pool)),1)}
                for repo in picks:
                    new.append(Recommendation(repo.id, local_date, "keyword", rule.id, None, None, observed_at,
                                              rank=ranks[repo.id]))
                    seen.add(repo.id)
                new_sections.append(("keyword", rule.id))

            snapshots = [
                StarSnapshot(repo.id, local_date, repo.stars, observed_at)
                for repo in fetched.values()
            ]
            self.store.commit_daily(
                local_date, list(fetched.values()), snapshots, new,
                completed_at=observed_at, completed_sections=new_sections,
                growth_coverage=growth_coverage,
            )
        except GitHubRequestError as exc:
            self.store.save_refresh_failure(observed_at, str(exc))
            saved_date = self.store.latest_successful_date()
            saved = self.store.daily_recommendations(saved_date) if saved_date else []
            return self._result(saved_date or local_date, saved, "error", True, str(exc), ())

        return self._result(
            local_date, self.store.daily_recommendations(local_date),
            "ok", False, "更新完成", tuple(notes),
        )

    def _budget_call(self, method, budget, *args):
        if not budget.can_spend("core", 1, self.clock()):
            raise GitHubRequestError("请求额度已达到预留值或单次更新时间限制")
        automatic = getattr(self.client, "budget", None) is budget
        if not automatic:
            budget.spend("core", 1, self.clock())
        result = method(*args)
        remaining = getattr(self.client, "core_remaining", None)
        if remaining is not None:
            budget.core_remaining = remaining
        return result

    def _refresh_discovered(self, local_date, observed_at):
        date.fromisoformat(local_date)
        observed = datetime.fromisoformat(observed_at)
        if observed.tzinfo is None:
            raise ValueError("更新时刻必须包含时区")
        if isinstance(self.client, GitHubClient):
            self.client.renew_expired_quotas(observed.timestamp())
        budget = RequestBudget(getattr(self.client, "core_remaining", None),
                               getattr(self.client, "search_remaining", None), self.clock()+self.REFRESH_SECONDS)
        if hasattr(self.client, "budget"):
            self.client.budget, self.client.clock = budget, self.clock
        completed = self.store.daily_sections(local_date)
        existing = self.store.daily_recommendations(local_date)
        rules = [r for r in self.store.list_keywords() if r.enabled]
        pending_rules = [r for r in rules if ("keyword",r.id) not in completed]
        has_growth = ("growth",None) in completed
        target_stat_date = (observed.astimezone(timezone.utc).date()-timedelta(days=1)).isoformat()
        saved_coverage = self.store.growth_coverage(local_date)
        date_upgrading = bool(has_growth and local_date == observed.date().isoformat()
                              and saved_coverage and saved_coverage.stat_date
                              and saved_coverage.stat_date < target_stat_date
                              and saved_coverage.metric_basis == 'github_daily_new')
        repairing = self.store.growth_repair_pending(local_date)
        if date_upgrading:
            repairing = False
        original_growth = [r for r in existing if r.section == 'growth']
        repair_dates = {r.metric_date for r in original_growth}
        repair_date = next(iter(repair_dates)) if len(repair_dates) == 1 else None
        if repairing or date_upgrading:
            has_growth = False
        reserved = self.store.growth_reserved_ids(local_date) if repairing or date_upgrading else set()
        growth_seen = (self.store.seen_repo_ids_before(local_date) | reserved
                       if repairing or date_upgrading else self.store.seen_repo_ids())
        fetched, notes, daily, evidences, raw_weeks = {}, [], {}, {}, {}
        checks, new, sections = [], [], []
        coverage = None
        failed = 0
        try:
            if repairing and (repair_date is None or observed.astimezone(timezone.utc) <
                              datetime.fromisoformat(repair_date+'T00:00:00+00:00') + timedelta(days=1)):
                raise GitHubRequestError('旧榜统计日尚不可核实，保留已有项目；新发现补齐待重试')
            # Refresh current numbers first; saved recommendation rank/delta remains frozen.
            saved = self.store.repositories_for_ids([r.repo_id for r in existing])
            for record in existing:
                refreshed = self._budget_call(self.client.get_repository,budget,saved[record.repo_id].full_name)
                if refreshed.id != record.repo_id:
                    raise GitHubRequestError("GitHub 仓库身份已变化，保留上次记录")
                fetched[refreshed.id] = refreshed
            report = None
            candidates = []
            if not has_growth or pending_rules:
                # Discovery has its own time slice, so slow external sources or a
                # large authenticated search allowance cannot starve growth scoring.
                # Reuse the budget object to retain observed quotas and probe state.
                refresh_deadline = budget.deadline
                budget.deadline = min(refresh_deadline, self.clock()+self.DISCOVERY_SECONDS)
                try:
                    report = self.discovery.discover(pending_rules,local_date,budget)
                finally:
                    budget.deadline = refresh_deadline
                notes.extend(report.notes)
                for batch in report.batches:
                    self.store.save_discovery_batch(batch)
                followed = [r for r, _, _ in self.store.followed_repositories()]
                recent = self.store.recent_growth_repositories(local_date)
                tracked = self.store.tracked_repositories(limit=100)
                saved_lanes = []
                for source, repositories in (("followed", followed), ("recent_growth", recent), ("tracked", tracked)):
                    lane = tuple(DiscoveryCandidate(r.full_name, r.id, (source,), observed_at)
                                 for r in repositories)
                    self.store.save_discovery_batch(DiscoveryBatch(source, lane, None, True, ()))
                    saved_lanes.append(lane)
                # Round-robin work, including the oldest unscored catalog entry, avoids
                # spending every day's allowance on the same external hot prefix.
                fresh_by_id = {c.repo_id:c for c in report.candidates if c.repo_id is not None}
                fresh_lanes = {}
                for candidate in report.candidates:
                    fresh_lanes.setdefault(candidate.source_names[0], []).append(candidate)
                catalog = [replace(c, cached_repo=None) for c in self.store.catalog_candidates(3000)]
                keys = set()
                lanes = [saved_lanes[0], catalog, *fresh_lanes.values(), *saved_lanes[1:]]
                for row in zip_longest(*lanes):
                    for candidate in row:
                        if candidate is None:
                            continue
                        candidate = fresh_by_id.get(candidate.repo_id, candidate)
                        key = (candidate.repo_id, candidate.full_name.casefold())
                        if key not in keys:
                            keys.add(key); candidates.append(candidate)
                # Fresh official search metadata costs no additional core lookup. Keep all
                # of it for keyword sorting even if the separate growth quota runs out.
                for candidate in report.candidates:
                    if (candidate.cached_repo is not None and candidate.repo_id == candidate.cached_repo.id
                            and candidate.full_name == candidate.cached_repo.full_name):
                        fetched[candidate.repo_id] = candidate.cached_repo
            names = {r.full_name.casefold(): r for r in fetched.values()}
            for candidate in candidates:
                if not budget.can_spend("core",1,self.clock()):
                    notes.append("请求预算或五分钟限制已到，未核算候选留待下一次")
                    break
                try:
                    repo = names.get(candidate.full_name.casefold()) or candidate.cached_repo
                    if repo is None:
                        repo = self._budget_call(self.client.get_repository,budget,candidate.full_name)
                    if candidate.repo_id is not None and candidate.repo_id != repo.id:
                        failed += 1
                        notes.append("候选名称对应的仓库 ID 已变化，未覆盖原身份")
                        continue
                    names[repo.full_name.casefold()] = repo
                    fetched[repo.id] = repo
                    resolved = replace(candidate,repo_id=repo.id,cached_repo=repo,
                                       evidence=tuple(replace(e,repo_id=repo.id) for e in candidate.evidence))
                    self.store.save_discovery_batch(DiscoveryBatch("verified",(resolved,),None,True,()))
                    evidences.setdefault(repo.id,[]).extend(resolved.evidence)
                    if not has_growth and not repo.archived and repo.id not in daily:
                        weeks = self._budget_call(self.client.star_history_weeks, budget, repo.full_name)
                        days = confirmed_star_days(weeks,observed)
                        raw_weeks[repo.id] = weeks
                        expected_date = repair_date if repairing else target_stat_date
                        day = next((d for d in days if d.stat_date == expected_date),None)
                        if day is not None:
                            daily[repo.id] = day
                        self.store.mark_catalog_scored(repo.id,observed_at)
                except (GitHubRequestError,ValueError) as exc:
                    failed += 1
                    notes.append(str(exc))
            # Hitting the bounded work limit is not a failed update once official
            # growth has been verified. Publish that measured subset and its real
            # coverage; unscored catalog entries remain available for later work.
            if self.clock() >= budget.deadline and not (daily or has_growth):
                raise GitHubRequestError("更新时间已到限制，保留上次结果")
            if not fetched:
                raise GitHubRequestError("未取得可核实的 GitHub 项目，保留上次结果")
            if not has_growth:
                stat_date = max((d.stat_date for d in daily.values()),default=None)
                if not repairing and stat_date != target_stat_date:
                    raise GitHubRequestError(f'尚未取得 {target_stat_date} 完整 UTC 日的官方增长数据，保留上次结果')
                if repairing and (repair_date is None or stat_date != repair_date):
                    raise GitHubRequestError('旧榜统计日尚不可核实，保留已有项目；新发现补齐待重试')
                slots = select_growth_slots(list(fetched.values()),daily,stat_date,
                                            growth_seen,reserved) if stat_date else []
                if date_upgrading and sum(s.display_role == 'new' for s in slots) < 5:
                    raise GitHubRequestError(f'{target_stat_date} 尚未补齐五个新项目，保留上次结果；请稍后重试')
                if stat_date:
                    for repo in list(fetched.values()):
                        day = daily.get(repo.id)
                        if day is not None and day.stat_date != stat_date:
                            day = None
                        source_records = evidences.get(repo.id) or self.store.source_evidence(repo.id)
                        comparisons = [c for e in (source_records or [None])
                                       for c in compare_source(e,repo,day,observed_at,"UTC")]
                        conflicts = [c for c in comparisons if c.status == "conflict"]
                        if conflicts and budget.can_spend("core",1,self.clock()):
                            try:
                                if any(c.metric == "daily_added" for c in conflicts):
                                    weeks = self._budget_call(self.client.star_history_weeks, budget, repo.full_name)
                                    again = confirmed_star_days(weeks,observed)
                                    raw_weeks[repo.id] = weeks
                                    day = next((d for d in again if d.stat_date == stat_date),day)
                                    if day is not None:
                                        daily[repo.id] = day
                                else:
                                    again = self._budget_call(self.client.get_repository,budget,repo.full_name)
                                    if again.id != repo.id:
                                        raise GitHubRequestError("复查发现仓库身份变化")
                                    repo = again;fetched[repo.id] = repo
                                comparisons = [c for e in (source_records or [None])
                                               for c in compare_source(e,repo,day,observed_at,"UTC")]
                            except (GitHubRequestError,ValueError) as exc:
                                notes.append(f"来源差异复查未完成：{exc}")
                        checks.extend(comparisons)
                    slots = [s for s in select_growth_slots(list(fetched.values()),daily,stat_date,growth_seen)
                             if s.pick.repo.id not in reserved]
                    if repairing and sum(s.display_role == 'new' for s in slots) < 5:
                        count = sum(s.display_role == 'new' for s in slots)
                        raise GitHubRequestError(f'同一统计日仅核实 {count} 个新项目，保留旧榜，补齐待重试')
                    for slot in slots:
                        pick=slot.pick
                        new.append(Recommendation(pick.repo.id,local_date,"growth",None,pick.delta,None,
                                                  observed_at,pick.metric_basis,pick.metric_date,
                                                  slot.rank,slot.display_role))
                    if sum(s.display_role=="new" for s in slots)<5:
                        notes.append("可核实且历史未推荐的新增长项目不足 5 个")
                    basis="github_daily_new"
                else:
                    baselines=self.store.snapshots_before(local_date,list(fetched))
                    picks=select_growth([r for r in fetched.values() if r.id not in self.store.seen_repo_ids()],baselines)
                    for pick in picks:
                        new.append(Recommendation(pick.repo.id,local_date,"growth",None,pick.delta,
                           pick.baseline_at,observed_at,pick.metric_basis,pick.metric_date,None,"new"))
                    basis="local_snapshot" if picks and picks[0].delta is not None else "pending"
                    notes.append("未取得保守完整统计日，增长记录建立中")
                sources=tuple(sorted({s for c in candidates for s in c.source_names}))
                coverage=GrowthCoverage(len({(c.repo_id,c.full_name.casefold()) for c in candidates}),
                    sum(d.stat_date==stat_date for d in daily.values()),sources,stat_date,basis,
                    self.store.catalog_count(),failed,tuple(dict.fromkeys(notes)))
                sections.append(("growth",None))
            display_growth=[r for r in (new if repairing or date_upgrading else (*existing,*new)) if r.section=="growth"]
            growth_slots=[GrowthSlot(GrowthPick(fetched[r.repo_id],r.star_delta,r.baseline_at,
                                              r.metric_basis or "pending",r.metric_date),r.rank or 0,
                                    r.display_role or "new") for r in display_growth if r.repo_id in fetched]
            allocation=allocate_keyword_groups(rules,{r.id:list(fetched.values()) for r in pending_rules},
                                               self.store.seen_repo_ids(),growth_slots)
            new=[replace(r,matched_keyword_ids=allocation.growth_matches.get(r.repo_id,()))
                 if r.section=="growth" else r for r in new]
            for rule in sorted(pending_rules,key=lambda r:r.id):
                picks=allocation.groups.get(rule.id,())
                for repo in picks:
                    new.append(Recommendation(repo.id,local_date,"keyword",rule.id,None,None,observed_at,
                                              rank=allocation.ranks[rule.id][repo.id]))
                if len(picks)<5:
                    notes.append(f"关键词「{rule.term}」符合条件的新项目不足 5 个")
                sections.append(("keyword",rule.id))
            commit = (self.store.commit_growth_date_update if date_upgrading else
                      self.store.commit_growth_repair if repairing else self.store.commit_daily)
            correction = {'expected_stat_date':saved_coverage.stat_date} if date_upgrading else {}
            commit(local_date,list(fetched.values()),
                [StarSnapshot(r.id,local_date,r.stars,observed_at) for r in fetched.values()],new,
                completed_at=observed_at,completed_sections=sections,growth_coverage=coverage,cross_checks=checks,
                official_weeks=raw_weeks,**correction)
        except GitHubRequestError as exc:
            self.store.save_refresh_failure(observed_at,str(exc))
            saved_date=self.store.latest_successful_date()
            saved=self.store.daily_recommendations(saved_date) if saved_date else []
            return self._result(saved_date or local_date,saved,"error",True,str(exc),tuple(dict.fromkeys(notes)))
        return self._result(local_date,self.store.daily_recommendations(local_date),"ok",False,"更新完成",
                            tuple(dict.fromkeys(notes)))

    def _has_core_budget(self) -> bool:
        remaining = getattr(self.client, "core_remaining", None)
        return remaining is None or remaining > self.CORE_RESERVE

    def _has_discovery_budget(self) -> bool:
        """Leave at least one core request to measure a discovered candidate."""
        remaining = getattr(self.client, "core_remaining", None)
        return remaining is None or remaining > self.CORE_RESERVE + 1

    def _search(self, query: str, *, page: int = 1) -> list[Repository]:
        if getattr(self.client, "search_remaining", None) == 0:
            raise GitHubRateLimitError(getattr(self.client, "reset_at", None))
        return self.client.search(query, page=page, per_page=self.PAGE_SIZE)

    def _growth_candidates(
        self, cutoff: str, local_date: str, has_today_growth: bool, notes: list[str],
        *, search_reserve: int = 0,
    ) -> tuple[list[Repository], list[str]]:
        if has_today_growth:
            return [], []
        buckets: dict[str, list[Repository]] = {
            "created": [], "pushed": [], "trending": [], "recent": [], "tracked": [],
        }
        sources: list[str] = []
        for category, query in (("created", f"created:>={cutoff}"), ("pushed", f"pushed:>={cutoff}")):
            remaining = getattr(self.client, "search_remaining", None)
            if remaining is not None and remaining <= search_reserve:
                notes.append("GitHub 搜索额度有限，增长候选来源已缩小")
                break
            buckets[category] = self._search(query)
        if buckets["created"] or buckets["pushed"]:
            sources.append("search")

        known_ids = {repo.id for group in buckets.values() for repo in group}
        for stored in self.store.recent_growth_repositories(local_date):
            if stored.id in known_ids:
                continue
            if not self._has_discovery_budget():
                notes.append("GitHub 请求额度有限，昨日榜单项目尚未重新核算")
                break
            refreshed = self.client.get_repository(stored.full_name)
            buckets["recent"].append(refreshed)
            known_ids.add(refreshed.id)
        if buckets["recent"]:
            sources.append("tracked")

        if self.trending is not None:
            try:
                names = self.trending.repo_names()
            except TrendingUnavailable:
                notes.append("GitHub Trending 暂不可用，候选范围已缩小")
            else:
                known_names = {repo.full_name for group in buckets.values() for repo in group}
                for name in names[:self.MAX_TRENDING_LOOKUPS]:
                    if name in known_names or not self._has_discovery_budget():
                        continue
                    repo = self.client.get_repository(name)
                    buckets["trending"].append(repo)
                    known_names.add(repo.full_name)
                if buckets["trending"]:
                    sources.append("trending")
                elif names and not self._has_discovery_budget():
                    notes.append("GitHub 请求额度有限，Trending 候选尚未纳入核算")

        for stored in self.store.tracked_repositories(limit=20):
            if stored.id in known_ids:
                continue
            if not self._has_discovery_budget():
                break
            refreshed = self.client.get_repository(stored.full_name)
            buckets["tracked"].append(refreshed)
            known_ids.add(refreshed.id)
        if buckets["tracked"] and "tracked" not in sources:
            sources.append("tracked")
        found: dict[int, Repository] = {}
        for row in zip_longest(*(buckets[name] for name in (
            "recent", "created", "pushed", "trending", "tracked",
        ))):
            for repo in row:
                if repo is not None:
                    found.setdefault(repo.id, repo)
        return list(found.values()), sources

    def _result(
        self,
        local_date: str,
        records: list[Recommendation],
        status: str,
        stale: bool,
        message: str,
        notes: tuple[str, ...],
    ) -> RefreshResult:
        repositories = self.store.repositories_for_ids([item.repo_id for item in records])
        updated_at = self.store.daily_updated_at(local_date) or max(
            (item.observed_at for item in records), default=None
        )
        if any(item.section == "growth" and item.star_delta is None for item in records):
            notes = tuple(dict.fromkeys((*notes, "首次采样：正在建立增长记录")))
        return RefreshResult(
            local_date, tuple(records), repositories, updated_at, status, stale, message, notes,
            self.store.growth_coverage(local_date),
        )
