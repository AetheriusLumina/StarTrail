"""Pure ranking rules; no network or database access."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import KeywordRule, OfficialStarWeek, Repository, StarDay, StarSnapshot


@dataclass(frozen=True, slots=True)
class GrowthPick:
    repo: Repository
    delta: int | None
    baseline_at: str | None
    metric_basis: str = "pending"
    metric_date: str | None = None


@dataclass(frozen=True, slots=True)
class GrowthSlot:
    pick: GrowthPick
    rank: int
    display_role: str


@dataclass(frozen=True, slots=True)
class KeywordAllocation:
    groups: dict[int, tuple[Repository, ...]]
    growth_matches: dict[int, tuple[int, ...]]
    notes: tuple[str, ...]
    ranks: dict[int, dict[int, int]]


def keyword_excluded_ids(historical_ids, occupied_ids, own_ids=()):
    """Only manual AI may revisit its own currently displayed keyword projects."""
    return (set(historical_ids) | set(occupied_ids)) - set(own_ids)


def allocate_keyword_groups(rules: list[KeywordRule], candidates: dict[int, list[Repository]],
                            historical_ids: set[int], growth_slots: list[GrowthSlot]) -> KeywordAllocation:
    active = sorted((r for r in rules if r.enabled), key=lambda r: r.id)
    matches = {slot.pick.repo.id: tuple(r.id for r in active if matches_keyword(slot.pick.repo,r))
               for slot in growth_slots}
    matches = {repo_id: ids for repo_id,ids in matches.items() if ids}
    excluded = keyword_excluded_ids(historical_ids, (s.pick.repo.id for s in growth_slots))
    groups, notes, ranks = {}, [], {}
    for rule in active:
        ranked = select_keyword(candidates.get(rule.id,[]), rule, set(),
                                limit=len(candidates.get(rule.id,[])))
        positions = {repo.id: position for position, repo in enumerate(ranked, 1)}
        picks = tuple(repo for repo in ranked if repo.id not in excluded)[:5]
        groups[rule.id] = picks
        ranks[rule.id] = {repo.id: positions[repo.id] for repo in picks}
        excluded.update(r.id for r in picks)
        if len(picks)<5:
            notes.append(f"关键词「{rule.term}」符合条件的新项目不足 5 个（本次 {len(picks)} 个）")
    return KeywordAllocation(groups,matches,tuple(notes),ranks)


def confirmed_star_days(weeks: list[OfficialStarWeek], observed_at: datetime) -> list[StarDay]:
    """Keep UTC Sunday-labelled days only after their full day has ended."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("统计时间必须包含时区")
    observed = observed_at.astimezone(timezone.utc)
    result = {}
    for week in weeks:
        if (isinstance(week.week, bool) or not isinstance(week.week, int)
                or len(week.days) != 7 or any(
                    isinstance(count, bool) or not isinstance(count, int) or count < 0
                    for count in week.days)):
            raise ValueError("官方 Star 周记录格式无效")
        try:
            first = datetime.fromtimestamp(week.week, timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("官方 Star 周记录日期无效") from exc
        if first.weekday() != 6 or first.time().isoformat() != "00:00:00":
            continue
        for offset, count in enumerate(week.days):
            label = first + timedelta(days=offset)
            if label + timedelta(days=1) > observed:
                continue
            key = label.date().isoformat()
            if key in result and result[key] != count:
                raise ValueError("官方 Star 记录存在相互冲突的日期")
            result[key] = count
    return [StarDay(day, count) for day, count in sorted(result.items())]


def select_growth_slots(repos: list[Repository], daily: dict[int, StarDay],
                        stat_date: str, seen_ids: set[int],
                        reserved_ids: set[int] = frozenset()) -> list[GrowthSlot]:
    ranked = select_daily_growth(repos, daily, stat_date, limit=len(repos))
    slots = []
    new_count = 0
    for rank, pick in enumerate(ranked, start=1):
        if pick.repo.id in reserved_ids:
            continue
        if pick.repo.id in seen_ids:
            if rank <= 5:
                slots.append(GrowthSlot(pick, rank, "old"))
        elif new_count < 5:
            slots.append(GrowthSlot(pick, rank, "new"))
            new_count += 1
        if rank >= 5 and new_count >= 5:
            break
    return slots


def latest_complete_day(days: list[StarDay], today_utc: str) -> StarDay | None:
    complete = [item for item in days if item.stat_date < today_utc]
    return max(complete, key=lambda item: item.stat_date, default=None)


def select_daily_growth(
    repos: list[Repository],
    daily: dict[int, StarDay],
    stat_date: str,
    limit: int = 5,
) -> list[GrowthPick]:
    picks = [
        GrowthPick(repo, day.added, None, "github_daily_new", stat_date)
        for repo in _eligible_unique(repos, set())
        if (day := daily.get(repo.id)) is not None
        and day.stat_date == stat_date
        and day.added > 0
    ]
    return sorted(picks, key=lambda item: (-item.delta, -item.repo.stars, item.repo.id))[:limit]


def select_growth(
    repos: list[Repository],
    baselines: dict[int, StarSnapshot],
    limit: int = 5,
) -> list[GrowthPick]:
    candidates = _eligible_unique(repos, set())
    if not any(repo.id in baselines for repo in candidates):
        return [GrowthPick(repo, None, None) for repo in sorted(candidates, key=lambda x: (-x.stars, x.id))[:limit]]

    ranked = []
    for repo in candidates:
        baseline = baselines.get(repo.id)
        if baseline is None:
            continue
        delta = repo.stars - baseline.stars
        if delta > 0:
            ranked.append(GrowthPick(repo, delta, baseline.observed_at, "local_snapshot"))
    if ranked:
        return sorted(ranked, key=lambda item: (-item.delta, -item.repo.stars, item.repo.id))[:limit]
    unmeasured = [repo for repo in candidates if repo.id not in baselines]
    return [GrowthPick(repo, None, None) for repo in
            sorted(unmeasured, key=lambda item: (-item.stars, item.id))[:limit]]


def select_keyword(
    repos: list[Repository],
    rule: KeywordRule,
    seen_ids: set[int],
    limit: int = 5,
) -> list[Repository]:
    matches = []
    for repo in _eligible_unique(repos, seen_ids):
        if matches_keyword(repo, rule):
            matches.append(repo)
    return sorted(matches, key=lambda item: (-item.stars, item.id))[:limit]


def matches_keyword(repo: Repository, rule: KeywordRule) -> bool:
    wanted = set(_words(rule.term))
    if not rule.enabled or repo.archived or repo.stars < rule.min_stars or not wanted:
        return False
    searchable = " ".join((repo.full_name, repo.description, *repo.topics))
    return wanted.issubset(set(_words(searchable)))


def _eligible_unique(repos: list[Repository], seen_ids: set[int]) -> list[Repository]:
    unique = {}
    for repo in repos:
        if repo.id not in seen_ids and not repo.archived:
            previous = unique.get(repo.id)
            if previous is None or repo.stars > previous.stars:
                unique[repo.id] = repo
    return list(unique.values())


def _words(value: str) -> list[str]:
    return re.findall(r"[^\W_]+", value.casefold())
