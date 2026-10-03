"""Bounded, credential-free public-source reads with same-origin redirects."""
import socket
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class SourceRequestError(Exception):
    def __init__(self, message, retry_after=None):
        self.retry_after = retry_after
        super().__init__(message)


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlparse(req.full_url), urlparse(newurl)
        if (new.scheme, new.netloc) != ("https", old.netloc):
            raise SourceRequestError("公开来源重定向离开允许的网站")
        if new.path.startswith(("/api/", "/login", "/signup", "/apikeys")):
            raise SourceRequestError("公开来源重定向到非公开入口")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_public(url, opener=None, budget=None, clock=time.monotonic):
    if budget is not None and not budget.can_spend("external", 1, clock()):
        raise SourceRequestError("单次更新已达到时间限制")
    timeout = min(20, budget.deadline - clock()) if budget is not None else 20
    request = Request(url, headers={"User-Agent": "GitHubRadar/0.2", "Accept": "text/html,application/json"})
    try:
        open_url = opener or build_opener(SameOriginRedirect()).open
        with open_url(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SourceRequestError("公开来源响应超过 10 MB 限制")
        return raw.decode("utf-8")
    except HTTPError as exc:
        raise SourceRequestError(f"公开来源请求失败（HTTP {exc.code}）",
                                 exc.headers.get("Retry-After")) from exc
    except (URLError, socket.timeout, OSError, HTTPException, UnicodeDecodeError) as exc:
        raise SourceRequestError("公开来源连接失败，请稍后重试") from exc
