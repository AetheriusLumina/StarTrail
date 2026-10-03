"""Compatibility rules for saved growth issues from before display roles."""
from dataclasses import replace

from .models import Recommendation


def classify_legacy_growth(records: list[Recommendation],
                           seen_before: set[int]) -> list[Recommendation]:
    return [replace(r, display_role='old' if r.repo_id in seen_before else 'new')
            if r.section == 'growth' and r.display_role is None else r for r in records]


def validate_growth_repair(records: list[Recommendation], local_date: str,
                           stat_date: str, reserved_ids: set[int]) -> None:
    ids = [r.repo_id for r in records]
    if len(ids) != len(set(ids)) or set(ids) & reserved_ids:
        raise ValueError('修复增长记录与已有项目重复')
    if any(r.local_date != local_date or r.section != 'growth'
           or r.metric_date != stat_date or r.metric_basis != 'github_daily_new'
           or r.star_delta is None or r.star_delta <= 0
           or r.rank is None or r.rank <= 0 or r.display_role not in ('old','new')
           for r in records):
        raise ValueError('修复增长记录的统计日期或排名不一致')
    if sum(r.display_role == 'new' for r in records) < 5:
        raise ValueError('修复增长记录未补齐五个新发现')
