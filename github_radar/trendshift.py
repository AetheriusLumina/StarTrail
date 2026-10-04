"""Read public daily/topic HTML, never hidden APIs or week/month endpoints."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from .discovery_types import DiscoveryBatch, DiscoveryCandidate, SourceEvidence, valid_repository_name
from .public_http import MAX_RESPONSE_BYTES, SourceRequestError, read_public

BASE = "https://trendshift.io"
DAILY_URL = BASE + "/github-trending-repositories?trending-range=1&trending-limit=100"


class PublicPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.links = [], []
        self.in_script = False
        self.href, self.anchor_text = None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.in_script = True
        if tag == "a":
            self.href = attrs.get("href", "")
            self.anchor_text = []

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False
        if tag == "a" and self.href is not None:
            self.links.append((self.href, "".join(self.anchor_text).strip()))
            self.href = None

    def handle_data(self, data):
        if self.href is not None:
            self.anchor_text.append(data)
        if self.in_script and data.startswith("self.__next_f.push("):
            try:
                value = json.loads(data[len("self.__next_f.push("):-1])
                if len(value) == 2 and isinstance(value[1], str):
                    self.scripts.append(value[1])
            except (ValueError, TypeError):
                pass

    def arrays(self, key):
        for script in self.scripts:
            for match in re.finditer('"' + key + '":', script):
                try:
                    value, end = json.JSONDecoder().raw_decode(script[match.end():])
                except ValueError:
                    continue
                if isinstance(value, list):
                    yield value, script[match.end() + end:match.end() + end + 350]


def parse_trendshift(html: str, source_url: str, observed_at: str) -> DiscoveryBatch:
    if len(html.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise SourceRequestError("Trendshift 页面超过响应限制")
    url = urlparse(source_url)
    if (url.scheme, url.netloc) != ("https", "trendshift.io"):
        raise SourceRequestError("Trendshift 来源地址不合法")
    page = PublicPage(); page.feed(html)
    topic = url.path.startswith("/topics/")
    homepage = url.path == "/"
    if topic:
        rows = [{"full_name": name} for href, name in page.links
                if re.fullmatch(r"/repositories/\d+", href) and valid_repository_name(name)]
        if not rows:
            arrays = [rows for rows, _ in page.arrays("repositories")
                      if rows and isinstance(rows[0], dict) and "full_name" in rows[0]]
            rows = arrays[0] if len(arrays) == 1 else []
        source, kind, period = "trendshift_topic", "none", "topic"
    else:
        key = "initialData" if homepage else "repositories"
        arrays = [(rows, after) for rows, after in page.arrays(key)
                  if rows and isinstance(rows[0], dict) and "full_name" in rows[0]
                  and (homepage or '"title":"GitHub trending repositories"' in after)]
        if len(arrays) != 1:
            raise SourceRequestError("Trendshift 榜单结构变化，未读取成功")
        rows = arrays[0][0]
        source = "trendshift_daily" if homepage else "trendshift_github_today"
        kind = "trendshift_daily" if homepage else "github_featured"
        period = "daily" if homepage or parse_qs(url.query).get("trending-range") == ["1"] else "all"
    candidates = {}
    for index, row in enumerate(rows[:1000], 1):
        name = row.get("full_name") if isinstance(row, dict) else None
        if not valid_repository_name(name) or name.casefold() in candidates:
            continue
        stamp = row.get("date")
        stat_date = stamp[:10] if isinstance(stamp, str) and re.match(r"\d{4}-\d{2}-\d{2}T", stamp) else None
        tags = tuple(t["name"] for t in row.get("tags", []) if isinstance(t, dict) and isinstance(t.get("name"), str))
        rank = row.get("rank", index) if homepage else index
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            rank = None
        total = row.get("repository_stars", row.get("watchers"))
        added = row.get("repository_stars_gained") if homepage else None
        evidence = SourceEvidence(source, name, source_url, observed_at, period, stat_date,
                                  kind, None if topic else rank,
                                  None if total is None else str(total),
                                  None if added is None else str(added), tags)
        candidates[name.casefold()] = DiscoveryCandidate(name, None, (source,), observed_at, evidence=(evidence,))
    if not candidates:
        raise SourceRequestError("Trendshift 未识别到公开项目，未读取成功")
    notes = (("主题首批候选，未核实全量分页；网站 AI 分类不是本软件 AI 精选",) if topic else
             (f"实际读取 {len(candidates)} 个项目；来源顺序与官方新增排名分别保存",))
    return DiscoveryBatch(source, tuple(candidates.values()), None, not topic, notes)


class TrendshiftClient:
    def __init__(self, opener=None, budget=None, clock=None):
        self.opener, self.budget, self.clock = opener, budget, clock
        self._directory = None

    def _read(self, url):
        kwargs = {"opener": self.opener, "budget": self.budget}
        if self.clock is not None:
            kwargs["clock"] = self.clock
        return read_public(url, **kwargs)

    def daily(self, observed_at: str) -> tuple[DiscoveryBatch, ...]:
        batches = []
        for url in (BASE + "/", DAILY_URL):
            try:
                batches.append(parse_trendshift(self._read(url), url, observed_at))
            except SourceRequestError as exc:
                source = "trendshift_daily" if url == BASE + "/" else "trendshift_github_today"
                batches.append(DiscoveryBatch(source, (), None, False, (str(exc),)))
        return tuple(batches)

    def topics(self,*,refresh=False):
        if refresh or self._directory is None:
            page = PublicPage();page.feed(self._read(BASE + "/topics"))
            self._directory = {label.lstrip("#").strip().casefold(): href for href, label in page.links
                               if re.fullmatch(r"/topics/[a-z0-9-]+", href)}
        return tuple(sorted(self._directory))

    def topic(self, term: str, observed_at: str) -> DiscoveryBatch:
        self.topics()
        href = self._directory.get(term.strip().casefold())
        if href is None:
            return DiscoveryBatch("trendshift_topic", (), None, False, ("未找到明确匹配的公开主题",))
        return parse_trendshift(self._read(BASE + href), BASE + href, observed_at)
