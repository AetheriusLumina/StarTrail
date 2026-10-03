"""Small, testable pieces of text shared by the desktop views."""

from dataclasses import dataclass

from .i18n import tr
from .models import KeywordRule, Recommendation, Repository
from .ranking import matches_keyword


@dataclass(frozen=True, slots=True)
class CardText:
    title: str
    stars: str
    growth: str
    observation: str
    description: str
    tags: tuple[str, ...]
    source: str
    growth_rank: int | None = None
    matched_keywords: tuple[str, ...] = ()
    display_role: str | None = None
    display_rank: int | None = None
    rank_source: str | None = None
    recommendation_date: str | None = None


def card_text(
    recommendation: Recommendation,
    repository: Repository,
    keyword_terms: dict[int, str],
    *,
    rank: int | None = None,
    keyword_rules: tuple[KeywordRule, ...] = (),
    language: str = "zh",
) -> CardText:
    if recommendation.section == "growth":
        if recommendation.star_delta is None:
            growth = tr(language, "正在建立增长记录")
        elif recommendation.metric_basis == "github_daily_new":
            growth = (f"+{recommendation.star_delta:,} new Stars" if language == "en"
                      else f"新增 {recommendation.star_delta:,} Star")
        else:
            growth = f"{recommendation.star_delta:+,} Star"
        source = tr(language, "近期 Star 增长")
    elif recommendation.section == "following":
        growth = ""
        source = tr(language, "我的关注")
    else:
        growth = ""
        term = keyword_terms.get(recommendation.keyword_id or 0,
                                 tr(language, "已保存关键词"))
        source = f"Keyword: {term}" if language == "en" else f"关键词：{term}"

    observation = ""
    if recommendation.metric_basis == "github_daily_new" and recommendation.metric_date:
        observation = (f"GitHub statistical date: {recommendation.metric_date}"
                       if language == "en" else f"GitHub 统计日：{recommendation.metric_date}")
    elif recommendation.baseline_at and recommendation.star_delta is not None:
        start = recommendation.baseline_at[:16].replace("T", " ")
        end = recommendation.observed_at[:16].replace("T", " ")
        observation = (f"Observation period: {start} → {end}"
                       if language == "en" else f"观察区间：{start} → {end}")

    saved_rank = recommendation.rank or rank
    growth_rank = (saved_rank if saved_rank is not None and saved_rank >= 1
                   and recommendation.section == "growth"
                   and recommendation.star_delta is not None
                   and recommendation.metric_basis in {"github_daily_new", "local_snapshot"}
                   else None)
    display_rank = (saved_rank if recommendation.section == "keyword"
                    and saved_rank is not None and saved_rank >= 1 else growth_rank)
    return CardText(
        title=repository.full_name,
        stars=f"★ {repository.stars:,}",
        growth=growth,
        observation=observation,
        description=repository.description.strip() or tr(language, "GitHub 暂无项目简介"),
        tags=repository.topics[:3],
        source=source,
        growth_rank=growth_rank,
        display_rank=display_rank,
        rank_source=recommendation.section if display_rank is not None else None,
        matched_keywords=(tuple(keyword_terms[i] for i in recommendation.matched_keyword_ids if i in keyword_terms)
                          if recommendation.matched_keyword_ids else
                          (tuple(rule.term for rule in keyword_rules
                                if matches_keyword(repository, rule))
                          if recommendation.section == "growth" else ())),
        display_role=recommendation.display_role,
        recommendation_date=recommendation.local_date or None,
    )


def saved_display_ranks(records: list[Recommendation]) -> dict[int, int]:
    """Fallback positions are per saved section/group, before a search filters rows."""
    counts: dict[tuple[str, int | None], int] = {}
    ranks = {}
    for record in records:
        key = record.section, record.keyword_id
        counts[key] = counts.get(key, 0) + 1
        ranks[record.repo_id] = record.rank or counts[key]
    return ranks
