"""Optional GitHub Trending discovery; no ranking numbers come from HTML."""

import re
from http.client import HTTPException
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.request import Request, urlopen


class TrendingUnavailable(Exception):
    """The optional Trending page could not be read reliably."""


class _TrendingParser(HTMLParser):
    _REPO_PATH = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_card = False
        self.in_heading = False
        self.names: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "article" and "Box-row" in values.get("class", "").split():
            self.in_card = True
        elif self.in_card and tag == "h2":
            self.in_heading = True
        elif self.in_heading and tag == "a":
            match = self._REPO_PATH.fullmatch(values.get("href", ""))
            if match:
                name = f"{match.group(1)}/{match.group(2)}"
                key = name.casefold()
                if key not in self._seen:
                    self.names.append(name)
                    self._seen.add(key)

    def handle_endtag(self, tag):
        if tag == "h2":
            self.in_heading = False
        elif tag == "article":
            self.in_card = False
            self.in_heading = False


class TrendingClient:
    URL = "https://github.com/trending?since=daily"

    def __init__(self, opener=urlopen):
        self._opener = opener

    def repo_names(self) -> list[str]:
        request = Request(self.URL, headers={"User-Agent": "GitHubRadar/0.1", "Accept": "text/html"})
        try:
            with self._opener(request, timeout=15) as response:
                html = response.read().decode("utf-8", errors="replace")
        except (URLError, OSError, TimeoutError, HTTPException) as exc:
            raise TrendingUnavailable("GitHub Trending 暂时无法读取") from exc
        parser = _TrendingParser()
        parser.feed(html)
        if not parser.names:
            raise TrendingUnavailable("GitHub Trending 页面结构已变化")
        return parser.names
