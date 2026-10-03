"""Public ClickHouse activity hints; these counts are never official Star growth."""
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from .discovery_types import DiscoveryBatch, DiscoveryCandidate, valid_repository_name
from .public_http import SourceRequestError, read_public


class FreeEventsClient:
    def __init__(self, opener=None, budget=None, clock=None):
        self.opener, self.budget, self.clock = opener, budget, clock

    def discover(self, stat_date: str, cursor: str | None = None) -> DiscoveryBatch:
        day = date.fromisoformat(stat_date)
        if day.isoformat() != stat_date:
            raise ValueError("活动日期无效")
        cursor_day, _, raw_offset = (cursor or "").partition(":")
        offset = int(raw_offset or 0) if cursor_day == stat_date else 0
        if not 0 <= offset <= 10000:
            raise ValueError("活动断点无效")
        tomorrow = (day + timedelta(days=1)).isoformat()
        sql = ("SELECT repo_name, count() AS captured_star_events FROM github_events "
               f"WHERE event_type = 'WatchEvent' AND created_at >= '{day}' "
               f"AND created_at < '{tomorrow}' GROUP BY repo_name "
               f"ORDER BY captured_star_events DESC, repo_name LIMIT 500 OFFSET {offset} FORMAT JSON")
        url = "https://play.clickhouse.com/?" + urlencode({"user": "play", "query": sql})
        kwargs = {"opener": self.opener, "budget": self.budget}
        if self.clock is not None:
            kwargs["clock"] = self.clock
        try:
            payload = json.loads(read_public(url, **kwargs))
            rows = payload["data"]
            if not isinstance(rows, list):
                raise ValueError("rows")
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceRequestError("公开活动数据格式变化，未读取成功") from exc
        observed = datetime.now(timezone.utc).isoformat()
        candidates = {}
        for row in rows[:500]:
            if not isinstance(row, dict):
                continue
            name, hint = row.get("repo_name"), row.get("captured_star_events")
            if (valid_repository_name(name) and isinstance(hint, int)
                    and not isinstance(hint, bool) and hint >= 0):
                candidates[name.casefold()] = DiscoveryCandidate(name, None, ("public_activity",),
                                                                 observed, hint)
        finished = len(rows) < 500
        return DiscoveryBatch("public_activity", tuple(candidates.values()),
                              None if finished else f"{stat_date}:{offset + 500}", finished,
                              (f"公开活动样本 {stat_date}；活动次数仅用于发现，不代表官方日新增 Star",))
