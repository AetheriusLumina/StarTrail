"""Small, unauthenticated GitHub REST client for public repositories."""

import json
import codecs
import base64
import binascii
import socket
from threading import RLock
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen, build_opener, getproxies

from .models import OfficialStarWeek, Repository, StarDay
from .discovery_types import DiscoveryBatch, DiscoveryCandidate, SearchPage, valid_repository_name
from .public_http import MAX_RESPONSE_BYTES, SameOriginRedirect, SourceRequestError
from .readme_types import ReadmeFetch


class GitHubRequestError(Exception):
    """A request failed or returned data the application cannot trust."""
    def __init__(self,message,*,status=None,path=None):
        super().__init__(message);self.status=status;self.path=path


class GitHubRateLimitError(GitHubRequestError):
    def __init__(self, reset_at: int | None, *, status=None, path=None):
        self.reset_at = reset_at
        super().__init__("GitHub 请求达到限流，请等待额度恢复后再试",status=status,path=path)


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, opener=urlopen, budget=None, clock=time.monotonic, token_provider=None, on_auth_failure=None, *, request_concurrency=4):
        if type(request_concurrency) is not int or not 1<=request_concurrency<=100:raise ValueError("GitHub concurrency must be between 1 and 100")
        self.request_concurrency=request_concurrency
        self.retry_not_before=0
        self._transport=None
        if opener is urlopen and not any(getproxies().get(k) for k in ('https','all')):
            from .github_transport import PooledHTTPSOpener
            self._transport=PooledHTTPSOpener(max_connections=request_concurrency);self._opener=self._transport
        else:self._opener=build_opener(SameOriginRedirect()).open if opener is urlopen else opener
        self.graphql_remaining=None;self.graphql_reset_at=None
        self.remaining: int | None = None
        self.core_remaining: int | None = None
        self.search_remaining: int | None = None
        self.reset_at: int | None = None
        self.core_reset_at: int | None = None
        self.search_reset_at: int | None = None
        self.budget, self.clock = budget, clock
        self._last_headers = {}
        self._quota_lock = RLock()
        self._auth_recovery_lock = RLock()
        self._auth_epoch = 0
        self.request_cache = None
        self.token_provider = token_provider
        self.on_auth_failure=on_auth_failure

    def _rate_error(self,exc,remaining,reset_at,path):
        headers={str(k).lower():str(v) for k,v in (exc.headers or {}).items()}
        retry=headers.get('retry-after','')
        secondary=exc.code==403 and bool(retry)
        if exc.code==403 and remaining!=0 and not secondary:
            try:secondary='rate limit' in exc.read(65537).decode('utf-8',errors='replace').casefold()
            except (OSError,ValueError):pass
        if exc.code!=429 and not (exc.code==403 and (remaining==0 or secondary)):return None
        # The shared pool stays bounded; subsequent waves and refreshes use fewer
        # workers. No retry is sent before the server's recovery time.
        now=time.time()
        primary=headers.get('x-ratelimit-remaining')=='0' or (remaining==0 and reset_at is not None and not secondary)
        reset_at=int(now)+int(retry) if retry.isdecimal() else reset_at if remaining==0 else int(now)+60
        reset_at=reset_at or int(now)+60
        with self._quota_lock:
            # Many already-issued requests can report the same cooldown. One
            # burst reduces the next wave once, rather than 100 -> 1 instantly.
            # An hourly quota is a count limit, not evidence of excessive concurrency.
            if not primary and now>=self.retry_not_before:
                self.request_concurrency=max(1,self.request_concurrency//2)
            self.retry_not_before=max(self.retry_not_before,reset_at)
        return GitHubRateLimitError(reset_at,status=exc.code,path=path)

    def close(self):
        if self._transport:self._transport.close()

    def _open_authenticated(self,request,timeout):
        with self._quota_lock:
            if time.time()<self.retry_not_before:raise GitHubRateLimitError(self.retry_not_before,path=request.full_url)
        try:return self._opener(request,timeout=timeout)
        except HTTPError as exc:
            authorization=request.get_header('Authorization')
            if exc.code!=401 or not authorization:raise
            exc.close()
            # A delayed 401 from the previous credential must not reopen the
            # fresh identity's reserved quota after another reader recovered it.
            with self._auth_recovery_lock:
                epoch=getattr(request,'_radar_auth_epoch',self._auth_epoch)
                if epoch==self._auth_epoch:
                    if self.on_auth_failure:self.on_auth_failure(authorization.removeprefix('Bearer '))
                    self._auth_epoch+=1
                    with self._quota_lock:
                        self.remaining=self.core_remaining=self.search_remaining=None
                        self.reset_at=self.core_reset_at=self.search_reset_at=None
                        self.graphql_remaining=self.graphql_reset_at=None
                        if self.budget is not None:self.budget.reset_identity()
                headers={k:v for k,v in request.header_items() if k.lower()!='authorization'}
                updated=self._authorization() if self.on_auth_failure else {}
                if updated.get('Authorization')!=authorization:headers.update(updated)
                request._radar_auth_epoch=self._auth_epoch
                if self.budget is not None:
                    api_path=urlparse(request.full_url).path
                    resource='external' if api_path=='/graphql' else 'search' if api_path.startswith('/search/') else 'core'
                    try:self.budget.spend(resource,1,self.clock())
                    except ValueError as error:raise GitHubRequestError('GitHub 请求预算或更新时间已到限制') from error
                    timeout=min(timeout,self.budget.deadline-self.clock())
            retry=Request(request.full_url,data=request.data,headers=headers,method=request.get_method())
            # One recovery only; never loop on a rejected credential.
            return self._opener(retry,timeout=timeout)

    def _authorization(self):
        token = self.token_provider() if self.token_provider else None
        return {"Authorization": "Bearer " + token} if token else {}

    def search(
        self, query: str, page: int = 1, per_page: int = 100, sort: str = "stars"
    ) -> list[Repository]:
        result = self.search_page(query, page, per_page, sort)
        if result.incomplete_results:
            raise GitHubRequestError("GitHub 搜索结果不完整，请稍后再试")
        return list(result.items)

    def search_page(self, query: str, page: int = 1, per_page: int = 100,
                    sort: str = "stars") -> SearchPage:
        if not 1 <= page <= 10 or not 1 <= per_page <= 100:
            raise ValueError("GitHub 搜索页码或每页数量无效")
        params = urlencode(
            {"q": query, "sort": sort, "order": "desc", "page": page, "per_page": per_page}
        )
        payload = self._get_json(f"/search/repositories?{params}")
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise GitHubRequestError("GitHub 搜索结果格式有误")
        total = payload.get("total_count", len(payload["items"]))
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise GitHubRequestError("GitHub 搜索结果数量格式有误")
        items=tuple(self._repository(item) for item in payload['items'])
        if self.request_cache:
            for item,raw in zip(items,payload['items']):
                parts=item.full_name.split('/')
                self.request_cache.put(f"/repos/{quote(parts[0],safe='')}/{quote(parts[1],safe='')}",{key:raw[key] for key in ('id','full_name','html_url','description','topics','language','stargazers_count','archived') if key in raw})
        return SearchPage(items,total,payload.get('incomplete_results') is True)

    def public_repository_page(self, since: int | None) -> DiscoveryBatch:
        if since is not None and (isinstance(since, bool) or not isinstance(since, int) or since < 0):
            raise ValueError("公开仓库游标无效")
        path = "/repositories" + ("?" + urlencode({"since": since}) if since is not None else "")
        payload = self._get_json(path)
        if not isinstance(payload, list):
            raise GitHubRequestError("公开仓库目录格式变化")
        observed = datetime.now(timezone.utc).isoformat()
        candidates = []
        for row in payload:
            if (isinstance(row, dict) and valid_repository_name(row.get("full_name"))
                    and isinstance(row.get("id"), int) and not isinstance(row["id"], bool) and row["id"] > 0):
                candidates.append(DiscoveryCandidate(row["full_name"], row["id"],
                                                     ("github_public_catalog",), observed))
        link = {str(k).lower(): v for k,v in self._last_headers.items()}.get("link", "")
        cursor = None
        for segment in link.split(","):
            if 'rel="next"' not in segment:
                continue
            target = urlparse(segment.split("<")[-1].split(">")[0])
            raw = parse_qs(target.query).get("since", [""])[0]
            if target.scheme == "https" and target.netloc == "api.github.com" and target.path == "/repositories" and raw.isdecimal():
                cursor = raw
        return DiscoveryBatch("github_public_catalog", tuple(candidates), cursor, cursor is None,
                              ("公开仓库目录持续积累；单批不是全站覆盖",))

    def get_repositories_batch(self,names):
        # This GraphQL operation is a read-only query. Retry one dropped
        # connection, never authorization, quota, schema or redirect failures.
        for attempt in range(2):
            try:return self._get_repositories_batch_once(names)
            except GitHubRequestError as exc:
                transport=exc.status is None and not isinstance(exc.__cause__,HTTPError) and isinstance(exc.__cause__,(URLError,TimeoutError,ConnectionError,HTTPException,OSError))
                if attempt or not transport or (self.budget is not None and not self.budget.can_spend('external',1,self.clock())):raise

    def _get_repositories_batch_once(self,names):
        """Bounded official totals/metadata. This is NOT a daily-Star batch."""
        names=tuple(dict.fromkeys(names))
        if not 1<=len(names)<=20 or any(not valid_repository_name(n) for n in names):raise ValueError('批量仓库名称无效')
        authorization=self._authorization()
        if not authorization:raise GitHubRequestError('批量元数据需要有效GitHub授权')
        if self.graphql_reset_at is not None and time.time()>=self.graphql_reset_at:self.graphql_remaining=None;self.graphql_reset_at=None
        if self.graphql_remaining==0:raise GitHubRateLimitError(self.graphql_reset_at)
        fields='databaseId nameWithOwner url description stargazerCount isArchived primaryLanguage { name } repositoryTopics(first:100) { nodes { topic { name } } }'
        parts=[]
        for i,name in enumerate(names):
            owner,repo=name.split('/');parts.append('r'+str(i)+': repository(owner:'+json.dumps(owner)+',name:'+json.dumps(repo)+') { '+fields+' }')
        payload={'query':'query { '+ ' '.join(parts)+' }'}
        timeout=20
        if self.budget is not None:
            if not self.budget.can_spend('external',1,self.clock()):raise GitHubRequestError('GitHub批量请求时间预算已到')
            timeout=min(timeout,self.budget.deadline-self.clock())
        request=Request(self.BASE_URL+'/graphql',data=json.dumps(payload).encode(),headers={'User-Agent':'GitHubRadar/0.1','Content-Type':'application/json','Accept':'application/json',**authorization},method='POST')
        request._radar_auth_epoch=self._auth_epoch
        try:
            with self._open_authenticated(request,timeout) as response:
                self._capture_limit(response.headers,'/graphql',request._radar_auth_epoch)
                raw=response.read(MAX_RESPONSE_BYTES+1)
                if len(raw)>MAX_RESPONSE_BYTES:raise GitHubRequestError('GitHub批量响应超过大小限制')
            body=json.loads(raw.decode('utf-8'))
            if isinstance(body,dict) and any(isinstance(e,dict) and e.get('type')=='RATE_LIMITED' for e in (body.get('errors') or ())):
                if not all(isinstance(e,dict) and e.get('type')=='RATE_LIMITED' for e in body['errors']):raise GitHubRequestError('GitHub批量元数据存在独立错误；未当作额度范围成功')
                raise GitHubRateLimitError(self.graphql_reset_at,path='/graphql')
            if not isinstance(body,dict) or not isinstance(body.get('data'),dict):raise GitHubRequestError('GitHub批量元数据未完整返回；未当作成功')
            aliases={'r'+str(i) for i in range(len(names))}
            if not aliases.issubset(body['data']):raise GitHubRequestError('GitHub批量元数据未完整返回；未当作成功')
            # A missing repository is a per-identity condition, not a failure
            # of unrelated aliases. Callers retry its stable ID via REST.
            for error in body.get('errors') or ():
                path=error.get('path') if isinstance(error,dict) else None
                if not isinstance(path,list) or len(path)!=1 or path[0] not in aliases or error.get('type')!='NOT_FOUND' or body['data'][path[0]] is not None:
                    raise GitHubRequestError('GitHub批量元数据未完整返回；未当作成功')
            result={}
            for i,name in enumerate(names):
                row=body['data'].get('r'+str(i))
                if row is None:continue
                repo=self._repository({'id':row['databaseId'],'full_name':row['nameWithOwner'],'html_url':row['url'],'description':row['description'],'stargazers_count':row['stargazerCount'],'archived':row['isArchived'],'language':(row.get('primaryLanguage') or {}).get('name'),'topics':[n['topic']['name'] for n in row['repositoryTopics']['nodes']]})
                result[name.casefold()]=repo
                if self.request_cache:
                    rest={'id':repo.id,'full_name':repo.full_name,'html_url':repo.html_url,'description':repo.description,'stargazers_count':repo.stars,'archived':repo.archived,'language':repo.language,'topics':list(repo.topics)}
                    self.request_cache.put('/repos/'+ '/'.join(quote(part,safe='') for part in repo.full_name.split('/')),rest)
            return result
        except HTTPError as exc:
            self._capture_limit(exc.headers,'/graphql',request._radar_auth_epoch)
            limited=self._rate_error(exc,self.graphql_remaining,self.graphql_reset_at,'/graphql')
            if limited:raise limited from exc
            raise GitHubRequestError(f'GitHub批量请求失败（HTTP {exc.code}）',status=exc.code,path='/graphql') from exc
        except (URLError,socket.timeout,TimeoutError,OSError,HTTPException,SourceRequestError) as exc:raise GitHubRequestError('连接GitHub批量接口失败，请检查网络') from exc
        except (ValueError,KeyError,TypeError) as exc:raise GitHubRequestError('GitHub批量元数据格式无效') from exc

    def get_repository(self, full_name: str) -> Repository:
        parts = full_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("仓库名称应为 owner/repository")
        path = f"/repos/{quote(parts[0], safe='')}/{quote(parts[1], safe='')}"
        return self._repository(self._get_json(path))

    def get_repository_by_id(self, repo_id: int) -> Repository:
        if isinstance(repo_id, bool) or not isinstance(repo_id, int) or repo_id <= 0:
            raise ValueError('仓库 ID 无效')
        result = self._repository(self._get_json(f'/repositories/{repo_id}'))
        if result.id != repo_id:
            raise GitHubRequestError('GitHub 返回的仓库 ID 不一致。')
        return result

    def fetch_readme_by_id(self, repo_id: int, etag: str | None = None,
                           max_bytes: int = 524288) -> ReadmeFetch:
        if isinstance(repo_id, bool) or not isinstance(repo_id, int) or repo_id <= 0:
            raise ValueError('仓库 ID 无效')
        return self.fetch_readme(repo_id, etag, max_bytes)

    def fetch_readme(self, full_name: str, etag: str | None = None,
                     max_bytes: int = 524288) -> ReadmeFetch:
        by_id = isinstance(full_name, int) and not isinstance(full_name, bool) and full_name > 0
        if (not by_id and not valid_repository_name(full_name)) or not 1 <= max_bytes <= 524288:
            raise ValueError('README 请求参数无效')
        self.renew_expired_quotas()
        if self.core_remaining == 0: raise GitHubRateLimitError(self.core_reset_at)
        timeout = 20
        if self.budget is not None:
            try: self.budget.spend('core', 1, self.clock())
            except ValueError as exc: raise GitHubRequestError('GitHub 请求预算或更新时间已到限制') from exc
            timeout = min(timeout, self.budget.deadline - self.clock())
        path = f'/repositories/{full_name}/readme' if by_id else '/repos/' + '/'.join(quote(part, safe='') for part in full_name.split('/')) + '/readme'
        headers = {'User-Agent':'GitHubRadar/0.1', 'Accept':'application/vnd.github+json' if by_id else 'application/vnd.github.raw+json',
                   'X-GitHub-Api-Version':'2022-11-28', **self._authorization()}
        if etag: headers['If-None-Match'] = etag
        request = Request(self.BASE_URL + path, headers=headers)
        try:
            with self._open_authenticated(request, timeout=timeout) as response:
                self._capture_limit(response.headers, path)
                # JSON has base64 expansion and metadata; both wire and decoded text are bounded.
                wire_limit = max_bytes * 2 + 16384 if by_id else max_bytes
                raw = response.read(wire_limit + 1)
                source_url = None
                if by_id:
                    if len(raw) > wire_limit:
                        raise GitHubRequestError('README 文件超过读取大小限制，请在 GitHub 查看原文。')
                    payload = json.loads(raw.decode('utf-8'))
                    if not isinstance(payload, dict) or payload.get('encoding') != 'base64':
                        raise GitHubRequestError('README 文件过大或内容格式不支持，请在 GitHub 查看原文。')
                    content, file_path, source_url = payload.get('content'), payload.get('path'), payload.get('html_url')
                    if (not isinstance(content, str) or not isinstance(file_path, str)
                            or not file_path or any(p in ('', '.', '..') for p in file_path.split('/'))
                            or not isinstance(source_url, str)):
                        raise GitHubRequestError('README 源文件信息无效。')
                    source = urlparse(source_url)
                    parts = source.path.split('/')
                    if (source.scheme != 'https' or source.netloc != 'github.com'
                            or len(parts) < 6 or parts[3] != 'blob' or source.query or source.fragment
                            or not source.path.endswith('/' + quote(file_path, safe='/'))):
                        raise GitHubRequestError('README 源文件地址无效。')
                    raw = base64.b64decode(''.join(content.split()), validate=True)
                truncated = len(raw) > max_bytes
                raw = raw[:max_bytes]
                # A bounded read may split the final UTF-8 character; only that tail can be dropped.
                text = raw.decode('utf-8', errors='strict') if not truncated else codecs.getincrementaldecoder('utf-8')().decode(raw, final=False)
                return ReadmeFetch(text, response.headers.get('ETag'), False, truncated, source_url)
        except HTTPError as exc:
            self._capture_limit(exc.headers, path)
            if exc.code == 304: return ReadmeFetch(None, etag, True, False)
            if exc.code == 404: return ReadmeFetch(None, None, False, False)
            limited=self._rate_error(exc,self.core_remaining,self.core_reset_at,request.full_url)
            if limited:raise limited from exc
            raise GitHubRequestError(f'GitHub README 请求失败（HTTP {exc.code}）') from exc
        except UnicodeDecodeError as exc:
            raise GitHubRequestError('README 不是有效的 UTF-8 文本。') from exc
        except (json.JSONDecodeError, binascii.Error) as exc:
            raise GitHubRequestError('README 内容格式无效。') from exc
        except (URLError, socket.timeout, TimeoutError, OSError, HTTPException, SourceRequestError) as exc:
            raise GitHubRequestError('连接 GitHub 失败，请检查网络') from exc

    def readme_excerpt(self, full_name: str, max_chars: int = 2400) -> str | None:
        """Read only a bounded public README excerpt; a missing README is optional."""
        parts = full_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("仓库名称应为 owner/repository")
        if not 1 <= max_chars <= 6000:
            raise ValueError("README 摘录长度无效")
        path = f"/repos/{quote(parts[0], safe='')}/{quote(parts[1], safe='')}/readme"
        request = Request(
            f"{self.BASE_URL}{path}",
            headers={"User-Agent": "GitHubRadar/0.1",
                     "Accept": "application/vnd.github.raw+json",
                     "X-GitHub-Api-Version": "2022-11-28", **self._authorization()},
        )
        try:
            with self._open_authenticated(request, timeout=15) as response:
                self._capture_limit(response.headers, path)
                raw = response.read(max_chars * 4 + 1)
            return raw.decode("utf-8", errors="replace")[:max_chars] or None
        except HTTPError as exc:
            self._capture_limit(exc.headers, path)
            if exc.code == 404:
                return None
            limited=self._rate_error(exc,self.remaining,self.reset_at,path)
            if limited:raise limited from exc
            raise GitHubRequestError(f"GitHub README 请求失败（HTTP {exc.code}）") from exc
        except (URLError, socket.timeout, TimeoutError, OSError, HTTPException, SourceRequestError) as exc:
            raise GitHubRequestError("连接 GitHub 失败，请检查网络") from exc

    def star_history(self, full_name: str) -> list[StarDay]:
        """Legacy date expansion. Ranking must use confirmed_star_days instead."""
        result = []
        for week in self.star_history_weeks(full_name):
            first = datetime.fromtimestamp(week.week, timezone.utc).date()
            result.extend(StarDay((first + timedelta(days=offset)).isoformat(), count)
                          for offset, count in enumerate(week.days))
        return sorted(result, key=lambda item: item.stat_date)

    def has_cached_star_history(self,full_name):
        parts=full_name.split('/')
        if len(parts)!=2 or self.request_cache is None:return False
        path=f"/repos/{quote(parts[0],safe='')}/{quote(parts[1],safe='')}/stargazers/history?per_page=2"
        return self.request_cache.get(path) is not None

    def star_history_weeks(self, full_name: str) -> list[OfficialStarWeek]:
        parts = full_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("仓库名称应为 owner/repository")
        path = f"/repos/{quote(parts[0], safe='')}/{quote(parts[1], safe='')}/stargazers/history?per_page=2"
        payload = self._get_json(path)
        if not isinstance(payload, list):
            raise GitHubRequestError("GitHub Star 历史格式有误")
        result: list[OfficialStarWeek] = []
        for week in payload:
            if not isinstance(week, dict) or isinstance(week.get("week"), bool) or not isinstance(week.get("week"), int):
                raise GitHubRequestError("GitHub Star 历史格式有误")
            days = week.get("days")
            if not isinstance(days, list) or len(days) != 7 or any(
                isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in days
            ):
                raise GitHubRequestError("GitHub Star 历史格式有误")
            try:
                datetime.fromtimestamp(week["week"], timezone.utc)
            except (OverflowError, OSError, ValueError) as exc:
                raise GitHubRequestError("GitHub Star 历史日期无效") from exc
            result.append(OfficialStarWeek(week["week"], tuple(days)))
        return sorted(result, key=lambda item: item.week)

    def _get_json(self, path: str):
        cache=self.request_cache if path.startswith('/repos/') and (len(path.split('?')[0].split('/'))==4 or '/stargazers/history?' in path) else None
        cached=cache.get(path) if cache else None
        if cached is not None:return cached
        timeout = 20
        if self.budget is not None:
            resource = "search" if path.startswith("/search/") else "core"
            try:
                self.budget.spend(resource, 1, self.clock())
            except ValueError as exc:
                raise GitHubRequestError("GitHub 请求预算或更新时间已到限制") from exc
            timeout = min(timeout, self.budget.deadline - self.clock())
        with self._auth_recovery_lock:
            authorization=self._authorization();epoch=self._auth_epoch
        request = Request(
            f"{self.BASE_URL}{path}",
            headers={
                "User-Agent": "GitHubRadar/0.1",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                **authorization,
            },
        )
        request._radar_auth_epoch=epoch
        try:
            with self._open_authenticated(request, timeout=timeout) as response:
                self._capture_limit(response.headers, path, request._radar_auth_epoch)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise GitHubRequestError("GitHub 响应超过 10 MB 限制")
                payload=json.loads(raw.decode('utf-8'))
                if cache:
                    minimal=payload
                    if isinstance(payload,dict):minimal={key:payload[key] for key in ('id','full_name','html_url','description','topics','language','stargazers_count','archived') if key in payload}
                    cache.put(path,minimal)
                return payload
        except HTTPError as exc:
            self._capture_limit(exc.headers, path, request._radar_auth_epoch)
            limited=self._rate_error(exc,self.remaining,self.reset_at,path)
            if limited:raise limited from exc
            raise GitHubRequestError(f"GitHub 请求失败（HTTP {exc.code}）",status=exc.code,path=path) from exc
        except (URLError, socket.timeout, TimeoutError, OSError, HTTPException, SourceRequestError) as exc:
            raise GitHubRequestError("连接 GitHub 失败，请检查网络") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GitHubRequestError("GitHub 返回了无法读取的数据") from exc

    def _capture_limit(self, headers, path: str, epoch=None) -> None:
        with self._quota_lock:
            if epoch is not None and epoch!=self._auth_epoch:return
            self._last_headers = dict(headers.items())
            if self.budget is not None:
                self.budget.observe(headers)
            lowered = {str(key).lower(): value for key, value in headers.items()}
            self.remaining = self._header_int(lowered.get("x-ratelimit-remaining"))
            self.reset_at = self._header_int(lowered.get("x-ratelimit-reset"))
            resource = lowered.get("x-ratelimit-resource") or ("search" if path.startswith("/search/") else "core")
            if self.budget is not None and resource in ("core", "search"):
                self.remaining = getattr(self.budget, resource + "_remaining")
            if resource == "core":
                self.core_remaining = self.remaining
                self.core_reset_at = self.reset_at
            elif resource == "graphql":
                self.graphql_remaining=self.remaining;self.graphql_reset_at=self.reset_at
            elif resource == "search":
                self.search_remaining = self.remaining
                self.search_reset_at = self.reset_at

    def renew_expired_quotas(self, now=None):
        """A passed server reset permits one fresh probe, never assumes a quota."""
        now = time.time() if now is None else now
        for resource in ("core", "search", "graphql"):
            reset = getattr(self, resource + "_reset_at")
            if reset is not None and now >= reset:
                setattr(self, resource + "_remaining", None)
                setattr(self, resource + "_reset_at", None)

    @staticmethod
    def _header_int(value) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _repository(item) -> Repository:
        try:
            if not isinstance(item, dict):
                raise TypeError("repository is not an object")
            topics = item.get("topics") or []
            if not isinstance(topics, list):
                raise TypeError("topics is not a list")
            return Repository(
                id=int(item["id"]),
                full_name=str(item["full_name"]),
                html_url=str(item["html_url"]),
                description=str(item.get("description") or ""),
                topics=tuple(str(topic) for topic in topics),
                language=item.get("language"),
                stars=int(item["stargazers_count"]),
                archived=bool(item["archived"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubRequestError("GitHub 仓库数据缺少必要字段") from exc
