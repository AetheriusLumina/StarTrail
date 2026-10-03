"""Compare evidence without losing timestamps, precision or statistical boundaries."""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CrossCheck:
    repo_id: int
    metric: str
    status: str
    official_value: int | None
    source_value_text: str | None
    source_url: str | None
    official_observed_at: str
    source_observed_at: str | None
    stat_date: str | None
    reason: str


def _precision_match(text, value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", (text or "").replace(",", "").strip())
    if match is None or value is None:
        return None
    number, suffix = match.groups()
    scale = {"": 1, "k": 1000, "m": 1000000}[suffix.lower()]
    center = Decimal(number) * scale
    if not suffix:
        return Decimal(value) == center
    decimals = len(number.split(".")[1]) if "." in number else 0
    half_step = Decimal(scale) / (10 ** decimals) / 2
    return center - half_step <= value < center + half_step


def compare_source(evidence, repo, day, official_observed_at, official_day_boundary):
    checks = []
    for metric, value, raw in (("total_stars", repo.stars, evidence.total_stars_text if evidence else None),
                               ("daily_added", day.added if day else None, evidence.daily_added_text if evidence else None)):
        date_label = day.stat_date if metric == "daily_added" and day else None
        status, reason = "missing_source", "缺少可比较来源，保留官方数据"
        if evidence is not None and raw is not None:
            aligned = False
            if metric == "total_stars":
                try:
                    source_time = datetime.fromisoformat(evidence.observed_at.replace("Z", "+00:00"))
                    official_time = datetime.fromisoformat(official_observed_at.replace("Z", "+00:00"))
                    aligned = (source_time.tzinfo is not None and official_time.tzinfo is not None
                               and abs((source_time - official_time).total_seconds()) <= 300)
                except (ValueError, TypeError):
                    pass
                reason = "总 Star 观测时间不同，不能判定真实差异"
            else:
                aligned = (day is not None and evidence.period == "daily"
                           and evidence.stat_date == day.stat_date and evidence.day_boundary is not None
                           and evidence.day_boundary == official_day_boundary)
                reason = "统计日期或日界未对齐，不能合并日新增数据"
            matches = _precision_match(raw, value)
            if not aligned or matches is None:
                status = "unaligned"
            else:
                status = "consistent" if matches else "conflict"
                reason = "在来源展示精度内一致" if matches else "观测数值存在差异，可能为来源延迟，双方数值均保留"
        checks.append(CrossCheck(repo.id, metric, status, value, raw,
                                  evidence.source_url if evidence else None, official_observed_at,
                                  evidence.observed_at if evidence else None, date_label, reason))
    return tuple(checks)
